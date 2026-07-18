"""
v0.2.1-rc6, requisito 3: una respuesta malformada o fuera de esquema del
proveedor primario dispara como máximo UN reintento controlado (con el
proveedor alternativo) -- nunca más, y nunca queda la tarea atascada en
'parsing'. Si el reintento también sale mal, termina en 'needs_review'.

Uso: python3 sidecar/tests/test_extraction_malformed_json.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _fake_ai_clients as fake  # noqa: E402


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    import ai_router as ai_router_mod
    return db_mod, tasks_mod, worker_mod, ai_router_mod


class TestExtractionMalformedJson(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_malformado_en_primario_reintenta_una_vez_con_el_alternativo_y_se_recupera(self):
        # Primario (openai, default de la matriz): malformado. Alternativo
        # (claude): válido -- exactamente UN reintento, y termina bien.
        claude_client, openai_client = self._install(
            openai_behavior=["malformed"],
            claude_behavior=[fake.valid_extraction(domain="cfo", intent="register_expense", amount=5000,
                                                     date="18/07/2026", concept="algo")],
        )

        result = self.tasks.create_task("malformado-1", "marianela", None, "Gasté $5.000 en algo")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "ready")  # register_expense con todos los datos -> simple -> ready
        self.assertEqual(openai_client.call_count, 1)
        self.assertEqual(claude_client.call_count, 1)

    def test_malformado_en_ambos_termina_en_needs_review_nunca_en_parsing(self):
        claude_client, openai_client = self._install(
            openai_behavior=["malformed"],
            claude_behavior=["malformed"],
        )
        result = self.tasks.create_task("malformado-2", "marianela", None, "texto raro")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "needs_review")
        self.assertNotEqual(final["state"], "parsing")
        self.assertIsNotNone(final["error_message"])
        # el presupuesto se respetó: 1 al primario + 1 al alternativo, nunca más.
        self.assertEqual(openai_client.call_count, 1)
        self.assertEqual(claude_client.call_count, 1)

    def test_json_invalido_de_verdad_tambien_cuenta_como_malformado(self):
        """`_FakeOpenAICompletions` con 'malformed' ya devuelve un content que
        no parsea como JSON -- esto prueba explícitamente esa rama de
        `_try_extract` (json.JSONDecodeError), no solo forma inválida."""
        claude_client, openai_client = self._install(
            openai_behavior=["malformed"],
            claude_behavior=[fake.valid_extraction()],
        )
        outcome = self.ai_router.extract("cualquier cosa")
        self.assertEqual(outcome["outcome"], "success")
        self.assertEqual(outcome["provider"], "claude")
        self.assertEqual(outcome["fell_back_from"], "openai")
        self.assertIn("JSON inválido", outcome["attempts"][0]["error"])

    def _install(self, openai_behavior, claude_behavior):
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router, claude_behavior=claude_behavior, openai_behavior=openai_behavior,
        )
        return claude_client, openai_client


if __name__ == "__main__":
    unittest.main()
