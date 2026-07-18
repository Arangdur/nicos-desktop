"""
Punto 6 de la revisión post-v0.2.1: cada tarea clasificada guarda con qué
versión/hash exactos de policies/risk_policy.yaml se decidió su dominio/riesgo.
Corre el flujo real de clasificación (con ai_router.extract() stubeado, mismo
patrón que test_integration_dry_run.py -- no llama a la API real de Claude/
OpenAI) y confirma que la tarea resultante tiene policy_version/policy_hash
poblados y coincidentes con centro_mando_adapter.POLICY_VERSION/POLICY_HASH.

Uso: python3 sidecar/tests/test_policy_traceability.py
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
    import centro_mando_adapter as cma_mod
    import ai_router as ai_router_mod
    return db_mod, tasks_mod, worker_mod, cma_mod, ai_router_mod


class TestPolicyTraceability(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.cma, self.ai_router = _fresh_modules(self.tmp_db.name)

        def _stub_extract(raw_text):
            return {"ok": True, "provider": "stub-test", "data": {
                "domain": "cfo", "intent": "register_expense", "amount": 10000,
                "date": "2026-07-17", "concept": "algo", "evidence": raw_text,
            }}
        self._real_extract = self.ai_router.extract
        self.ai_router.extract = _stub_extract
        self.worker.ai_router.extract = _stub_extract

    def tearDown(self):
        self.ai_router.extract = self._real_extract
        os.unlink(self.tmp_db.name)

    def test_tarea_clasificada_guarda_policy_version_y_hash(self):
        result = self.tasks.create_task("policy-trace-1", "nicolas", None, "Gasté $10.000 en algo")
        task_id = result["task"]["task_id"]
        task = self.tasks.get_task_dict(task_id)

        self.worker._process_classification(task)

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "ready")
        self.assertIsNotNone(task["policy_version"])
        self.assertIsNotNone(task["policy_hash"])
        self.assertEqual(task["policy_version"], self.cma.POLICY_VERSION)
        self.assertEqual(task["policy_hash"], self.cma.POLICY_HASH)

    def test_policy_hash_cambia_si_el_yaml_cambia(self):
        # No reescribe el YAML real -- solo confirma que POLICY_HASH es
        # sensible al contenido exacto del archivo (sha256 del texto crudo),
        # comparando contra un hash calculado manualmente del mismo archivo.
        import hashlib
        with open(self.cma.RISK_POLICY_PATH, "rb") as f:
            contenido = f.read()
        hash_esperado = hashlib.sha256(contenido).hexdigest()
        self.assertEqual(self.cma.POLICY_HASH, hash_esperado)


if __name__ == "__main__":
    unittest.main()
