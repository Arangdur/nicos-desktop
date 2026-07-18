"""
Etapa 11 (actualizado en v0.2.1-rc1 al vocabulario de 5 estados del ledger de
ejecución, ver centro_mando_adapter.reconcile_execution_attempt) — simula el
escenario que más importaba resolver de toda la revisión: NicOS muere
(kill -9, apagón) justo en medio de una ejecución real, con la tarea en
'executing' y un intento que quedó en 'claimed' (nunca se llegó a invocar el
subprocess). Al "reiniciar" (llamar recover_orphaned_tasks() de nuevo, como
hace server.main() al arrancar), la tarea tiene que:
  1. terminar en 'needs_review', NUNCA reintentarse sola;
  2. la fila de execution_attempts pasar a 'effect_failed' -- certeza total,
     porque 'claimed' significa que ni siquiera se pudo haber reclamado el
     operation_id en el ledger de registrar_movimiento.py;
  3. NO haber ningún movimiento nuevo escrito en foto_financiera (coherente
     con el punto 2: es imposible que haya ocurrido).

El caso "SÍ se ejecutó pero NicOS murió antes de registrarlo" (attempt en
'effect_started' + operation_id presente en el ledger -> reconciliación
automática a 'completed') vive en test_execution_reconciliation.py, que es
más específico para ese escenario.

Usa base de datos y archivos temporales -- nunca toca nada real.

Uso: python3 sidecar/tests/test_crash_recovery.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    return db_mod, tasks_mod, worker_mod


class TestCrashRecovery(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _make_task_stuck_in_executing(self):
        result = self.tasks.create_task("crash-test-1", "nicolas", None, "Gasté $10.000 en algo")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "command": "registrar_movimiento.py",
                    "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 10000, "tipo": "gasto", "detalle": ""}}
        action_hash = self.tasks.compute_action_hash(prepared)

        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="simple", task_revision=1)
        self.tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})
        self.tasks.transition(task_id, "executing", "system")

        # Esto es lo que centro_mando_adapter.execute_action() hace ANTES de
        # invocar el subprocess real -- acá el test se detiene justo ahí,
        # simulando que el proceso murió antes de que el subprocess terminara
        # (o incluso antes de que arrancara), que es el peor caso: no hay
        # rastro de si el efecto externo ocurrió.
        attempt = self.tasks.create_execution_attempt(task_id, "crash-test-operation-id", executor="centro_mando_adapter")
        return task_id, attempt

    def test_tarea_interrumpida_termina_en_needs_review_no_en_completed(self):
        task_id, attempt = self._make_task_stuck_in_executing()

        task_antes = self.tasks.get_task_dict(task_id)
        self.assertEqual(task_antes["state"], "executing")

        running = self.tasks.get_running_execution_attempt(task_id)
        self.assertIsNotNone(running)
        self.assertEqual(running["execution_id"], attempt["execution_id"])

        # "Reinicio" del proceso.
        self.worker.recover_orphaned_tasks()

        task_despues = self.tasks.get_task_dict(task_id)
        self.assertEqual(task_despues["state"], "needs_review")

        self.assertIsNone(self.tasks.get_running_execution_attempt(task_id),
                           "el intento debería haber quedado marcado 'effect_failed', no seguir 'claimed'")

        conn = self.db.get_connection()
        row = conn.execute(
            "SELECT status FROM execution_attempts WHERE execution_id = ?", (attempt["execution_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "effect_failed")

    def test_no_reintenta_automaticamente(self):
        task_id, _ = self._make_task_stuck_in_executing()
        self.worker.recover_orphaned_tasks()

        # Un segundo "reinicio" (ej. si el proceso se cae de nuevo) no debe
        # volver a tocar esta tarea -- ya está en needs_review, que no es un
        # estado que recover_orphaned_tasks() ni _claim_next_task() procesen.
        estado_1 = self.tasks.get_task_dict(task_id)["state"]
        self.worker.recover_orphaned_tasks()
        estado_2 = self.tasks.get_task_dict(task_id)["state"]
        self.assertEqual(estado_1, "needs_review")
        self.assertEqual(estado_2, "needs_review")

        claimed = self.worker._claim_next_task()
        self.assertIsNone(claimed, "needs_review no debe ser reencolable automáticamente por el worker")

    def test_mensaje_le_dice_al_humano_que_verifique_manualmente(self):
        task_id, _ = self._make_task_stuck_in_executing()
        self.worker.recover_orphaned_tasks()

        events = self.tasks.get_task_events(task_id)
        ultimo = events[-1]
        detail = ultimo.get("detail_json")
        if isinstance(detail, str):
            import json
            detail = json.loads(detail)
        self.assertIn("aviso", detail)
        self.assertEqual(detail.get("reconciliacion"), "effect_failed")
        self.assertIn("no se ejecutó", detail["aviso"].lower())

    def test_tarea_en_ready_sin_ejecutar_se_reencola_sin_problema(self):
        # Contraste: una tarea aprobada pero que NUNCA llegó a 'executing'
        # (no hay execution_attempt) es segura de reencolar -- no hubo ningún
        # efecto externo posible todavía.
        result = self.tasks.create_task("crash-test-2", "nicolas", None, "Gasté $5.000 en otra cosa")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "command": "registrar_movimiento.py",
                    "args": {"fecha": "2026-07-17", "concepto": "otra cosa", "monto": 5000, "tipo": "gasto", "detalle": ""}}
        action_hash = self.tasks.compute_action_hash(prepared)
        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="simple", task_revision=1)
        self.tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "ready", "una tarea 'ready' sin execution_attempt no debe tocarse al recuperar")
        claimed = self.worker._claim_next_task()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["task_id"], task_id)


if __name__ == "__main__":
    unittest.main()
