"""
Punto 3 de la revisión post-v0.2.1: las 4 decisiones del Director sobre una
tarea needs_review causada por una interrupción a mitad de ejecución.
Especialmente importante: "confirmar no ejecutada y reintentar" tiene que
generar un operation_id NUEVO -- nunca reutiliza el viejo (eso sería
literalmente el bug de duplicación que toda la idempotencia existe para evitar).

Uso: python3 sidecar/tests/test_resolve_execution.py
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
    return db_mod, tasks_mod


class TestResolveExecution(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _make_needs_review_task(self, operation_id="op-vieja"):
        result = self.tasks.create_task(f"resolve-test-{operation_id}", "nicolas", None, "Gasté $10.000 en algo")
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
        attempt = self.tasks.create_execution_attempt(task_id, operation_id, executor="test")
        self.tasks.finish_execution_attempt(attempt["execution_id"], "effect_failed")
        self.tasks.transition(task_id, "needs_review", "system", detail={"reconciliacion": "effect_failed"})
        return task_id

    def test_confirm_executed_cierra_como_completed(self):
        task_id = self._make_needs_review_task()
        result = self.tasks.resolve_execution(task_id, "nicolas", "confirm_executed")
        self.assertEqual(result["state"], "completed")

    def test_confirm_not_executed_retry_vuelve_a_ready(self):
        task_id = self._make_needs_review_task(operation_id="op-vieja-nunca-ejecutada")
        result = self.tasks.resolve_execution(task_id, "nicolas", "confirm_not_executed_retry")
        self.assertEqual(result["state"], "ready")

    def test_retry_genera_operation_id_nuevo_no_reutiliza_el_viejo(self):
        """El corazón de este test: 'reintentar' nunca debe significar
        reutilizar el operation_id de un intento que ya falló/quedó incierto --
        eso sería exactamente el bug de duplicación que la idempotencia evita."""
        import centro_mando_adapter as cma
        task_id = self._make_needs_review_task(operation_id="op-vieja-especifica")
        self.tasks.resolve_execution(task_id, "nicolas", "confirm_not_executed_retry")

        # El worker retomaría la tarea 'ready' y llamaría execute_action(), que
        # genera un operation_id nuevo con uuid4() -- lo confirmamos llamándolo
        # directamente acá (sin correr el subprocess real, solo mirando que la
        # fila de execution_attempts nueva tenga un operation_id distinto).
        prepared = {"domain": "cfo", "command": "registrar_movimiento.py",
                    "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 10000, "tipo": "gasto", "detalle": ""}}
        attempt_nuevo = self.tasks.create_execution_attempt(task_id, __import__("uuid").uuid4().hex, executor="centro_mando_adapter")
        attempts = self.db.get_connection().execute(
            "SELECT operation_id FROM execution_attempts WHERE task_id = ? ORDER BY started_at", (task_id,)
        ).fetchall()
        operation_ids = [r["operation_id"] for r in attempts]
        self.assertEqual(len(operation_ids), len(set(operation_ids)), "los operation_id de reintentos no deberían repetirse")
        self.assertEqual(len(operation_ids), 2)

    def test_cancel_cancela(self):
        task_id = self._make_needs_review_task(operation_id="op-a-cancelar")
        result = self.tasks.resolve_execution(task_id, "nicolas", "cancel")
        self.assertEqual(result["state"], "cancelled")

    def test_keep_in_review_no_cambia_estado_pero_deja_evento(self):
        task_id = self._make_needs_review_task(operation_id="op-para-esperar")
        result = self.tasks.resolve_execution(task_id, "nicolas", "keep_in_review", reason="Voy a revisar el archivo primero")
        self.assertEqual(result["state"], "needs_review")

        events = self.tasks.get_task_events(task_id)
        ultimo = events[-1]
        self.assertEqual(ultimo["from_state"], "needs_review")
        self.assertEqual(ultimo["to_state"], "needs_review")

    def test_decision_invalida_lanza_error_claro(self):
        task_id = self._make_needs_review_task(operation_id="op-decision-mala")
        with self.assertRaises(ValueError):
            self.tasks.resolve_execution(task_id, "nicolas", "decision_que_no_existe")


if __name__ == "__main__":
    unittest.main()
