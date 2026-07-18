"""
Worker loop durable de NicOS Core — reemplaza los `threading.Thread` ad-hoc por
request (v0.2) por un único loop persistente que reencola desde SQLite. Esto es
lo que hace que una tarea sobreviva a que el proceso se reinicie a mitad de
camino (crash, `kill -9`, apagón de la Mac): el estado en `tasks.state` ES la
cola, no hace falta una cola separada — el worker simplemente vuelve a
preguntarle a la base qué hay pendiente en su próximo ciclo, sin depender de
que un thread efímero haya sobrevivido.

`recover_orphaned_tasks()` corre una sola vez al arrancar, antes del loop:
decide qué hacer con tareas que quedaron en un estado intermedio de una
corrida anterior. Desde v0.2.1-rc1, para tareas interrumpidas en 'executing'
no asume nada -- reconcilia contra el ledger durable de registrar_movimiento.py
(ver centro_mando_adapter.reconcile_execution_attempt) para saber si el
movimiento se llegó a registrar o no, en vez de mandar siempre a needs_review
con un mensaje genérico.
"""
import datetime
import json
import sys
import time
import traceback
import uuid

import ai_router
import centro_mando_adapter
import db
import tasks

POLL_INTERVAL_SECONDS = 1
QUEUED_STATES = ("received", "parsing", "ready")

# Identidad de ESTE proceso, generada una sola vez al importar el módulo.
# Usada por _claim_next_task() para que dos sidecars corriendo por error contra
# la misma base nunca puedan ejecutar la misma tarea a la vez (v0.2.1-rc1,
# punto 5 de la revisión). Modelo de amenaza real: una sola Mac, un solo
# Director -- esto cubre el caso de un sidecar huérfano que no se cerró bien
# tras un reinicio de Electron, no un sistema distribuido genérico.
WORKER_ID = uuid.uuid4().hex[:12]


def _clear_stale_locks():
    """Al arrancar, cualquier lock de una corrida anterior es por definición
    huérfano -- este proceso es, en el instante del arranque, la única
    instancia legítima corriendo. Se limpia antes de reconciliar nada más."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE tasks SET locked_by = NULL, locked_at = NULL WHERE state IN (?, ?, ?)",
        QUEUED_STATES,
    )
    conn.commit()


def recover_orphaned_tasks():
    _clear_stale_locks()
    for task in tasks.list_tasks():
        state = task["state"]
        if state == "executing":
            attempt = tasks.get_running_execution_attempt(task["task_id"])
            if attempt is None:
                # No debería pasar (execute_action siempre crea la fila antes
                # de todo), pero por las dudas no se asume nada.
                tasks.transition(
                    task["task_id"], "needs_review", "system",
                    detail={"motivo": "recuperación al reiniciar", "aviso": "No se encontró intento de ejecución asociado -- revisar manualmente."},
                )
                continue

            verdict = centro_mando_adapter.reconcile_execution_attempt(attempt)

            if verdict == "effect_confirmed":
                tasks.finish_execution_attempt(attempt["execution_id"], "effect_confirmed")
                tasks.transition(
                    task["task_id"], "completed", "system",
                    detail={
                        "motivo": "recuperación al reiniciar", "reconciliacion": "effect_confirmed",
                        "aviso": (
                            "El movimiento se había registrado correctamente antes de la "
                            "interrupción -- confirmado automáticamente contra el ledger de "
                            "operaciones de registrar_movimiento.py."
                        ),
                    },
                )
                sys.stderr.write(
                    f"[worker] tarea {task['task_id']} interrumpida pero YA ejecutada -> completed (reconciliado)\n"
                )
            else:
                tasks.finish_execution_attempt(
                    attempt["execution_id"], verdict,
                    error_message="Interrumpido por reinicio del proceso — no se reintenta automáticamente.",
                )
                aviso = (
                    "No se encontró evidencia de que el movimiento se haya registrado "
                    "(probablemente NO se ejecutó)."
                    if verdict == "effect_failed" else
                    "No se pudo verificar con certeza si el movimiento se registró -- "
                    "el ledger de operaciones no se pudo leer."
                )
                tasks.transition(
                    task["task_id"], "needs_review", "system",
                    detail={
                        "motivo": "recuperación al reiniciar", "reconciliacion": verdict,
                        "aviso": aviso + " Resolvé desde la Bandeja de tareas: confirmar ejecutada, "
                                 "confirmar no ejecutada y reintentar, cancelar, o mantener en revisión.",
                    },
                )
                sys.stderr.write(
                    f"[worker] tarea {task['task_id']} interrumpida a mitad de ejecución -> needs_review ({verdict})\n"
                )
        elif state in QUEUED_STATES:
            sys.stderr.write(f"[worker] tarea {task['task_id']} en '{state}' será reencolada\n")


def _claim_next_task():
    """Reclamo atómico vía UPDATE...WHERE (no SELECT-y-después-UPDATE, que
    dejaba una ventana de carrera entre leer y escribir). Si dos procesos
    compitieran por la misma tarea, SQLite serializa los UPDATE concurrentes
    (WAL + busy_timeout, ver db.py) -- solo uno ve rowcount==1."""
    conn = db.get_connection()
    now = datetime.datetime.utcnow().isoformat()
    for state in QUEUED_STATES:
        pending = tasks.list_tasks(state=state)
        for candidate in reversed(pending):  # list_tasks ordena DESC -> reversed = más viejo primero
            cur = conn.execute(
                "UPDATE tasks SET locked_by = ?, locked_at = ? WHERE task_id = ? AND state = ? "
                "AND (locked_by IS NULL OR locked_by = ?)",
                (WORKER_ID, now, candidate["task_id"], state, WORKER_ID),
            )
            conn.commit()
            if cur.rowcount == 1:
                return tasks.get_task_dict(candidate["task_id"])
    return None


def _find_prepared_action(task_id):
    events = tasks.get_task_events(task_id)
    for e in reversed(events):
        detail = e.get("detail_json")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {}
        if detail and "prepared_action" in detail:
            return detail["prepared_action"]
    return None


def _process_classification(task):
    """received/parsing -> classified -> (needs_information | pending_approval | ready)."""
    task_id = task["task_id"]
    try:
        if task["state"] == "received":
            tasks.transition(task_id, "parsing", "system")

        extraction = ai_router.extract(task["raw_text"])
        if not extraction.get("ok"):
            tasks.transition(
                task_id, "failed", "system",
                detail={"motivo": "extracción falló", "error": extraction.get("error")},
                error_message=extraction.get("error"),
            )
            return

        extracted = extraction["data"]
        domain = centro_mando_adapter.classify_request(extracted)
        if domain is None:
            tasks.transition(
                task_id, "needs_information", "system",
                detail={"pregunta": "No pude determinar si esto es de CFO o de Abate — ¿podés aclararlo?"},
                domain=extracted.get("domain"), intent=extracted.get("intent"), extracted_json=extracted,
            )
            return

        risk = centro_mando_adapter.evaluate_risk(domain, extracted.get("intent"), extracted)
        current = tasks.get_task_dict(task_id)
        new_revision = (current.get("task_revision") or 0) + 1
        tasks.transition(
            task_id, "classified", "ai",
            detail={"extraction_provider": extraction.get("provider"), "task_revision": new_revision},
            domain=domain, intent=extracted.get("intent"), extracted_json=extracted, risk_level=risk,
            task_revision=new_revision,
            # Trazabilidad de política (v0.2.1-rc1, punto 6): con qué versión/hash
            # exactos de policies/risk_policy.yaml se clasificó esta tarea.
            policy_version=centro_mando_adapter.POLICY_VERSION, policy_hash=centro_mando_adapter.POLICY_HASH,
        )

        prepared = centro_mando_adapter.prepare_action(domain, extracted.get("intent"), extracted)
        action_hash = tasks.compute_action_hash(prepared)

        if risk == "simple":
            tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                              detail={"prepared_action": prepared, "task_revision": new_revision})
        else:
            tasks.transition(task_id, "pending_approval", "system", action_version_hash=action_hash,
                              detail={"prepared_action": prepared, "task_revision": new_revision})
    except Exception as e:
        sys.stderr.write("[worker] ERROR clasificando: " + traceback.format_exc() + "\n")
        try:
            tasks.transition(task_id, "needs_review", "system", detail={"error": str(e)})
        except Exception:
            pass


def _process_execution(task):
    """ready -> executing -> completed | failed | needs_review."""
    task_id = task["task_id"]
    prepared = _find_prepared_action(task_id)
    if prepared is None:
        tasks.transition(task_id, "needs_review", "system",
                          detail={"error": "no se encontró la acción preparada en el historial"})
        return
    try:
        tasks.transition(task_id, "executing", "system")
        result = centro_mando_adapter.execute_action(task_id, prepared)
        centro_mando_adapter.record_result(task_id, "system", result)
    except Exception as e:
        sys.stderr.write("[worker] ERROR ejecutando: " + traceback.format_exc() + "\n")
        try:
            tasks.transition(task_id, "needs_review", "system", detail={"error": str(e)})
        except Exception:
            pass


def run_forever():
    recover_orphaned_tasks()
    sys.stderr.write("[worker] loop arrancado\n")
    while True:
        task = _claim_next_task()
        if task is None:
            time.sleep(POLL_INTERVAL_SECONDS)
            continue
        if task["state"] in ("received", "parsing"):
            _process_classification(task)
        elif task["state"] == "ready":
            _process_execution(task)
