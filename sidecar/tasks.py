"""
Máquina de estados de tareas de NicOS Core.

10 estados pedidos + cancelled:
  received -> parsing -> classified -> needs_information -> pending_approval
            -> ready -> executing -> completed | failed | needs_review
  (+ cancelled, alcanzable desde varios estados)

Cada transición se escribe en `task_events` (append-only) — esa tabla ES la
auditoría estructurada. `tasks` guarda solo el estado ACTUAL para consultas
rápidas; la verdad histórica completa está en `task_events`.

Idempotencia: `idempotency_key` es UNIQUE en `tasks` — crear una tarea con una
key que ya existe devuelve la tarea existente en vez de duplicarla (a nivel de
base de datos, no solo de aplicación, así que es robusta a condiciones de carrera).

Aprobación versionada: `action_version_hash` es el hash de la acción propuesta
en el momento de pedir aprobación. `approve_task` exige que el llamador pase
el hash que vio — si la tarea se reclasificó después (nuevo hash), el pedido
de aprobación con el hash viejo se rechaza y hay que volver a mostrarla.
"""
import datetime
import hashlib
import json
import sqlite3
import uuid

import db

ALLOWED_TRANSITIONS = {
    "received": {"parsing", "cancelled"},
    "parsing": {"classified", "failed", "cancelled"},
    "classified": {"needs_information", "pending_approval", "ready", "needs_review", "cancelled"},
    "needs_information": {"classified", "cancelled"},
    "pending_approval": {"ready", "needs_information", "cancelled"},
    "ready": {"executing", "cancelled"},
    "executing": {"completed", "failed", "needs_review"},
    "completed": set(),
    "failed": {"needs_review", "cancelled"},
    "needs_review": {"classified", "cancelled"},
    "cancelled": set(),
}


class InvalidTransition(Exception):
    pass


class StaleApproval(Exception):
    """La acción cambió desde que se pidió la aprobación — hay que re-aprobar."""
    pass


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def compute_action_hash(prepared_action: dict) -> str:
    canonical = json.dumps(prepared_action, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _row_to_dict(row):
    if row is None:
        return None
    d = dict(row)
    for key in ("extracted_json", "result_json"):
        if d.get(key):
            try:
                d[key] = json.loads(d[key])
            except (json.JSONDecodeError, TypeError):
                pass
    return d


def create_task(idempotency_key: str, submitted_by: str, device_id: str, raw_text: str,
                 attachment_path: str = None) -> dict:
    conn = db.get_connection()
    existing = conn.execute(
        "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
    ).fetchone()
    if existing is not None:
        return {"task": _row_to_dict(existing), "created": False}

    task_id = str(uuid.uuid4())
    now = _now_iso()
    try:
        conn.execute(
            "INSERT INTO tasks (task_id, idempotency_key, submitted_by, device_id, raw_text, "
            "attachment_path, state, created_at, updated_at) VALUES (?, ?, ?, ?, ?, ?, 'received', ?, ?)",
            (task_id, idempotency_key, submitted_by, device_id, raw_text, attachment_path, now, now),
        )
        conn.execute(
            "INSERT INTO task_events (task_id, from_state, to_state, actor, detail_json, created_at) "
            "VALUES (?, NULL, 'received', ?, ?, ?)",
            (task_id, submitted_by, json.dumps({"raw_text": raw_text}), now),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        # condición de carrera: otra request con la misma key ganó la inserción primero
        conn.rollback()
        existing = conn.execute(
            "SELECT * FROM tasks WHERE idempotency_key = ?", (idempotency_key,)
        ).fetchone()
        return {"task": _row_to_dict(existing), "created": False}

    return {"task": _row_to_dict(get_task(task_id)), "created": True}


def get_task(task_id: str):
    conn = db.get_connection()
    return conn.execute("SELECT * FROM tasks WHERE task_id = ?", (task_id,)).fetchone()


def get_task_dict(task_id: str):
    return _row_to_dict(get_task(task_id))


def get_task_events(task_id: str):
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT * FROM task_events WHERE task_id = ? ORDER BY event_id ASC", (task_id,)
    ).fetchall()
    return [dict(r) for r in rows]


def list_tasks(state: str = None, submitted_by: str = None):
    conn = db.get_connection()
    query = "SELECT * FROM tasks WHERE 1=1"
    params = []
    if state:
        query += " AND state = ?"
        params.append(state)
    if submitted_by:
        query += " AND submitted_by = ?"
        params.append(submitted_by)
    query += " ORDER BY created_at DESC"
    rows = conn.execute(query, params).fetchall()
    return [_row_to_dict(r) for r in rows]


def transition(task_id: str, to_state: str, actor: str, detail: dict = None, **field_updates):
    """Transición genérica: valida el grafo de estados, actualiza `tasks`, y
    agrega la fila correspondiente en `task_events`. `field_updates` son columnas
    adicionales de `tasks` a actualizar en la misma operación (ej. domain, intent,
    extracted_json, risk_level, action_version_hash, result_json, error_message).
    """
    conn = db.get_connection()
    row = get_task(task_id)
    if row is None:
        raise ValueError(f"Tarea {task_id} no existe.")

    from_state = row["state"]
    allowed = ALLOWED_TRANSITIONS.get(from_state, set())
    if to_state not in allowed:
        raise InvalidTransition(
            f"No se puede pasar de '{from_state}' a '{to_state}'. "
            f"Transiciones válidas desde '{from_state}': {sorted(allowed)}"
        )

    now = _now_iso()
    set_clauses = ["state = ?", "updated_at = ?"]
    params = [to_state, now]
    for key, value in field_updates.items():
        set_clauses.append(f"{key} = ?")
        if key in ("extracted_json", "result_json") and value is not None and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, default=str)
        params.append(value)
    params.append(task_id)

    conn.execute(f"UPDATE tasks SET {', '.join(set_clauses)} WHERE task_id = ?", params)
    conn.execute(
        "INSERT INTO task_events (task_id, from_state, to_state, actor, detail_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, from_state, to_state, actor, json.dumps(detail or {}, ensure_ascii=False, default=str), now),
    )
    conn.commit()
    return get_task_dict(task_id)


def approve_task(task_id: str, approver_user_id: str, approved_action_hash: str):
    row = get_task(task_id)
    if row is None:
        raise ValueError(f"Tarea {task_id} no existe.")
    if row["action_version_hash"] != approved_action_hash:
        raise StaleApproval(
            "La acción cambió desde que se generó esta vista de aprobación — "
            "recargá la tarea y volvé a revisarla antes de aprobar."
        )
    now = _now_iso()
    return transition(
        task_id, "ready", approver_user_id,
        detail={"approved_action_hash": approved_action_hash},
        approved_by=approver_user_id, approved_at=now, approved_action_hash=approved_action_hash,
    )


def reject_task(task_id: str, approver_user_id: str, reason: str):
    return transition(task_id, "cancelled", approver_user_id, detail={"motivo_rechazo": reason})


def request_info(task_id: str, approver_user_id: str, question: str):
    return transition(task_id, "needs_information", approver_user_id, detail={"pregunta": question})
