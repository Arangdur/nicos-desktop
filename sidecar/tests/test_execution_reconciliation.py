"""
Punto 1-2 de la revisión post-v0.2.1: el operation_id tiene que quedar
asociado de forma durable a un recibo verificable (el ledger de
registrar_movimiento.py), no solo al estado en SQLite. Este test prueba los 3
desenlaces de reconcile_execution_attempt() y, más importante, el escenario
que motivó el pedido: el subprocess SÍ escribió el movimiento pero NicOS murió
antes de registrar el resultado -- la recuperación tiene que reconocerlo solo,
sin mandar la tarea a needs_review innecesariamente.

Usa base de datos y ledger temporales -- nunca toca nada real.

Uso: python3 sidecar/tests/test_execution_reconciliation.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path, tmp_ledger_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    os.environ["NICOS_OPERATION_LEDGER"] = tmp_ledger_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    import centro_mando_adapter as cma_mod
    return db_mod, tasks_mod, worker_mod, cma_mod


class TestExecutionReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_dir = tempfile.mkdtemp(prefix="nicos-reconcile-")
        self.tmp_ledger = os.path.join(self.tmp_dir, "OPERATION_IDS_PROCESADOS.txt")
        self.db, self.tasks, self.worker, self.cma = _fresh_modules(self.tmp_db.name, self.tmp_ledger)

    def tearDown(self):
        os.unlink(self.tmp_db.name)
        if os.path.exists(self.tmp_ledger):
            os.unlink(self.tmp_ledger)
        os.rmdir(self.tmp_dir)

    def _write_ledger(self, *operation_ids):
        with open(self.tmp_ledger, "w", encoding="utf-8") as f:
            for op_id in operation_ids:
                f.write(op_id + "\n")

    # ---- reconcile_execution_attempt() en aislamiento ----

    def test_claimed_siempre_effect_failed_sin_mirar_el_ledger(self):
        # Ni siquiera importa qué haya en el ledger -- si nunca se invocó el
        # subprocess, no pudo haber reclamado nada.
        self._write_ledger("op-fantasma-que-no-deberia-importar")
        attempt = {"status": "claimed", "operation_id": "op-nunca-invocado"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_failed")

    def test_effect_started_con_operation_id_en_ledger_es_confirmado(self):
        self._write_ledger("op-abc-123")
        attempt = {"status": "effect_started", "operation_id": "op-abc-123"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_confirmed")

    def test_effect_started_sin_operation_id_en_ledger_es_failed(self):
        self._write_ledger("op-otro-completamente-distinto")
        attempt = {"status": "effect_started", "operation_id": "op-abc-123"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_failed")

    def test_ledger_inexistente_es_failed_no_uncertain(self):
        # Que el archivo todavía no exista es legítimo (nunca se procesó nada
        # todavía) -- no es una falla de lectura.
        attempt = {"status": "effect_started", "operation_id": "op-abc-123"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_failed")

    def test_ledger_illegible_es_uncertain(self):
        # Simula una falla de I/O real: el "archivo" es en verdad un directorio.
        os.makedirs(self.tmp_ledger)
        try:
            attempt = {"status": "effect_started", "operation_id": "op-abc-123"}
            self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "uncertain")
        finally:
            os.rmdir(self.tmp_ledger)

    # ---- el escenario real que motivó el punto 1 ----

    def _make_task_stuck_executing(self, operation_id):
        result = self.tasks.create_task(f"reconcile-test-{operation_id}", "nicolas", None, "Gasté $10.000 en algo")
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
        attempt = self.tasks.create_execution_attempt(task_id, operation_id, executor="centro_mando_adapter")
        self.tasks.update_execution_attempt_status(attempt["execution_id"], "effect_started")
        return task_id, attempt

    def test_efecto_SI_ocurrio_pero_nicos_murio_antes_de_registrar_resultado(self):
        """Este es el gap real que señaló la revisión: el subprocess terminó de
        escribir en foto_financiera_*.md y en el ledger, pero el proceso de
        NicOS murió justo antes de llamar finish_execution_attempt(). Al
        reiniciar, tiene que reconocerse solo -- NUNCA quedar en needs_review
        por algo que en realidad sí se hizo."""
        task_id, attempt = self._make_task_stuck_executing("op-realmente-ejecutado")
        self._write_ledger("op-realmente-ejecutado")  # el subprocess SÍ llegó a escribir esto

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "completed", "debería auto-reconciliarse a completed, no quedar en needs_review")

        events = self.tasks.get_task_events(task_id)
        ultimo = events[-1]
        detail = json.loads(ultimo["detail_json"]) if isinstance(ultimo["detail_json"], str) else ultimo["detail_json"]
        self.assertEqual(detail.get("reconciliacion"), "effect_confirmed")

    def test_efecto_NO_ocurrio_queda_en_needs_review_con_veredicto_claro(self):
        task_id, attempt = self._make_task_stuck_executing("op-nunca-llego-a-escribir")
        # Ledger vacío -- el subprocess nunca llegó a reclamar este operation_id.

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "needs_review")

        events = self.tasks.get_task_events(task_id)
        detail = json.loads(events[-1]["detail_json"])
        self.assertEqual(detail.get("reconciliacion"), "effect_failed")
        self.assertIn("no se ejecutó", detail.get("aviso", "").lower())


if __name__ == "__main__":
    unittest.main()
