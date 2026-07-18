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

Desde 'parsing' (v0.2.1-rc6): antes de esta versión, solo se podía salir de
'parsing' hacia 'classified', 'failed' o 'cancelled' -- un dominio ambiguo
("unknown", un valor legítimo del propio schema de extracción de ai_router.py)
o una respuesta malformada no tenían ninguna transición válida disponible, así
que la tarea quedaba atascada en 'parsing' para siempre, reintentando la
extracción real en cada ciclo del worker (ver worker.py, MAX_EXTRACTION_ATTEMPTS
para el respaldo estructural adicional). Ahora 'parsing' también puede llegar a
'needs_information' (dominio ambiguo o campos insuficientes, sin más llamados
automáticos) y a 'needs_review' (ambos proveedores de IA fallaron, o se agotó
el presupuesto de reintentos) -- ninguna falla de clasificación puede quedar
sin una transición explícita y válida.
"""
import datetime
import hashlib
import json
import sqlite3
import uuid

import db

ALLOWED_TRANSITIONS = {
    # "needs_review" agregado (v0.2.1-rc6) para que el respaldo de
    # MAX_EXTRACTION_ATTEMPTS (ver worker.py) nunca pueda fallar: si por
    # cualquier motivo una tarea sigue en 'received' (nunca llegó siquiera a
    # 'parsing', ej. crasheó justo al arrancar, repetidas veces) al momento en
    # que se detecta que superó el límite de intentos, tiene que poder cortar
    # ahí mismo -- sin depender de haber llegado antes a 'parsing'.
    "received": {"parsing", "cancelled", "needs_review"},
    "parsing": {"classified", "failed", "cancelled", "needs_information", "needs_review"},
    "classified": {"needs_information", "pending_approval", "ready", "needs_review", "cancelled"},
    # "needs_information" -> "needs_information" (v0.2.1-rc6): si Nicolás
    # aporta información y TODAVÍA no alcanza (ver worker.provide_missing_info),
    # la tarea se queda en el mismo estado con un nuevo evento explicando qué
    # sigue faltando -- nunca sin registro de que se lo volvió a mirar.
    "needs_information": {"classified", "needs_information", "cancelled"},
    "pending_approval": {"ready", "needs_information", "cancelled"},
    "ready": {"executing", "cancelled"},
    "executing": {"completed", "failed", "needs_review"},
    "completed": set(),
    "failed": {"needs_review", "cancelled"},
    # "completed" y "ready" agregados en v0.2.1-rc1: resolve_execution() los usa
    # para que el Director cierre a mano una tarea needs_review causada por una
    # interrupción a mitad de ejecución (ver reconcile_execution_attempt en
    # centro_mando_adapter.py) -- "confirmar ejecutada" / "confirmar no
    # ejecutada y reintentar" respectivamente. Sigue sin haber reintento automático.
    "needs_review": {"classified", "cancelled", "completed", "ready"},
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


def approve_task(task_id: str, approver_user_id: str, approved_action_hash: str, approved_task_revision: int = None):
    """Exige que TANTO la revisión como el hash coincidan con los actuales
    (condición explícita post-revisión v0.2.1) — si la tarea se reclasificó
    (importe, fecha, dominio, intención, destinatario o acción distintos), el
    hash cambia y la revisión se incrementa; cualquiera de los dos desalineado
    invalida la aprobación."""
    row = get_task(task_id)
    if row is None:
        raise ValueError(f"Tarea {task_id} no existe.")
    if row["action_version_hash"] != approved_action_hash:
        raise StaleApproval(
            "La acción cambió desde que se generó esta vista de aprobación (hash distinto) — "
            "recargá la tarea y volvé a revisarla antes de aprobar."
        )
    if approved_task_revision is not None and row["task_revision"] != approved_task_revision:
        raise StaleApproval(
            f"La tarea se reclasificó desde que se generó esta vista de aprobación "
            f"(revisión {approved_task_revision} aprobada, revisión actual {row['task_revision']}) — "
            "recargá la tarea y volvé a revisarla antes de aprobar."
        )
    now = _now_iso()
    return transition(
        task_id, "ready", approver_user_id,
        detail={
            "task_revision": row["task_revision"], "action_hash": approved_action_hash,
            "decided_by": approver_user_id, "decision": "approved", "reason": None,
        },
        approved_by=approver_user_id, approved_at=now, approved_action_hash=approved_action_hash,
    )


def reject_task(task_id: str, approver_user_id: str, reason: str):
    row = get_task(task_id)
    return transition(
        task_id, "cancelled", approver_user_id,
        detail={
            "task_revision": row["task_revision"] if row else None, "action_hash": row["action_version_hash"] if row else None,
            "decided_by": approver_user_id, "decision": "rejected", "reason": reason,
        },
    )


def request_info(task_id: str, approver_user_id: str, question: str):
    row = get_task(task_id)
    return transition(
        task_id, "needs_information", approver_user_id,
        detail={
            "task_revision": row["task_revision"] if row else None, "action_hash": row["action_version_hash"] if row else None,
            "decided_by": approver_user_id, "decision": "info_requested", "reason": question,
        },
    )


# ---- execution_attempts: idempotencia del EFECTO EXTERNO, no solo de la tarea ----
# Ver centro_mando_adapter.py. Una fila acá se crea SIEMPRE antes de invocar el
# subprocess de registrar_movimiento.py — así una tarea que quedó 'executing' al
# reiniciar se puede distinguir de una que nunca llegó a intentar ejecutarse.

def create_execution_attempt(task_id: str, operation_id: str, executor: str) -> dict:
    conn = db.get_connection()
    execution_id = str(uuid.uuid4())
    now = _now_iso()
    conn.execute(
        "INSERT INTO execution_attempts (execution_id, task_id, operation_id, started_at, status, executor) "
        "VALUES (?, ?, ?, ?, 'claimed', ?)",
        (execution_id, task_id, operation_id, now, executor),
    )
    conn.commit()
    return {"execution_id": execution_id, "task_id": task_id, "operation_id": operation_id,
            "started_at": now, "status": "claimed", "executor": executor}


def update_execution_attempt_status(execution_id: str, status: str):
    """Cambio de estado SIN finished_at -- para el paso intermedio 'claimed' ->
    'effect_started' justo antes de invocar el subprocess real (ver
    centro_mando_adapter.execute_action). finish_execution_attempt() es para
    el desenlace final (effect_confirmed/effect_failed/uncertain)."""
    conn = db.get_connection()
    conn.execute("UPDATE execution_attempts SET status = ? WHERE execution_id = ?", (status, execution_id))
    conn.commit()


def finish_execution_attempt(execution_id: str, status: str, result_json: dict = None, error_message: str = None):
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE execution_attempts SET status = ?, finished_at = ?, result_json = ?, error_message = ? "
        "WHERE execution_id = ?",
        (status, now, json.dumps(result_json, ensure_ascii=False, default=str) if result_json else None,
         error_message, execution_id),
    )
    conn.commit()


def get_running_execution_attempt(task_id: str):
    """Para la recuperación al arrancar: ¿esta tarea 'executing' tiene un intento
    que quedó a mitad (status en 'claimed'/'effect_started', nunca se le puso
    finished_at)? El nombre se mantiene por compatibilidad con worker.py, aunque
    ya no busca literalmente status='running' (ver migración 003)."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM execution_attempts WHERE task_id = ? AND status IN ('claimed', 'effect_started') "
        "ORDER BY started_at DESC LIMIT 1",
        (task_id,),
    ).fetchone()
    return dict(row) if row else None


# ---- resolución del Director para tareas needs_review por interrupción ----
# (v0.2.1-rc1, punto 3 de la revisión). Cuatro decisiones posibles sobre una
# tarea que quedó needs_review porque una ejecución se interrumpió a mitad de
# camino (ver worker.recover_orphaned_tasks + centro_mando_adapter.reconcile_
# execution_attempt). Nunca reintenta con el MISMO operation_id -- "reintentar"
# siempre significa generar uno nuevo vía execute_action().

def resolve_execution(task_id: str, approver_user_id: str, decision: str, reason: str = None):
    row = get_task(task_id)
    if row is None:
        raise ValueError(f"Tarea {task_id} no existe.")
    detail = {"decided_by": approver_user_id, "decision": decision, "reason": reason}

    if decision == "confirm_executed":
        return transition(task_id, "completed", approver_user_id, detail=detail)
    if decision == "confirm_not_executed_retry":
        return transition(task_id, "ready", approver_user_id, detail=detail)
    if decision == "cancel":
        return transition(task_id, "cancelled", approver_user_id, detail=detail)
    if decision == "keep_in_review":
        log_execution_resolution_note(task_id, approver_user_id, reason)
        return get_task_dict(task_id)
    raise ValueError(
        f"Decisión '{decision}' no reconocida. Válidas: confirm_executed, "
        f"confirm_not_executed_retry, cancel, keep_in_review."
    )


def log_execution_resolution_note(task_id: str, actor: str, note: str):
    """Evento de auditoría SIN cambio de estado (from_state == to_state) --
    deja constancia de que el Director revisó una tarea needs_review y decidió
    esperar, para que no quede indistinguible de 'nadie la miró todavía'."""
    conn = db.get_connection()
    row = get_task(task_id)
    if row is None:
        raise ValueError(f"Tarea {task_id} no existe.")
    now = _now_iso()
    conn.execute(
        "INSERT INTO task_events (task_id, from_state, to_state, actor, detail_json, created_at) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (task_id, row["state"], row["state"], actor,
         json.dumps({"decision": "keep_in_review", "note": note}, ensure_ascii=False), now),
    )
    conn.commit()
