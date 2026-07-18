"""
v0.2.1-rc6, requisito de política de extracción: si ambos proveedores fallan
(cualquier combinación de error transitorio/auth/malformado), la tarea debe
terminar en un estado EXPLÍCITO ('needs_review' o 'failed'), nunca sin
transición. También cubre el caso "autenticación/configuración inválida:
error visible, sin bucle" -- un error de auth en el primario NO dispara el
alternativo (a propósito, ver ai_router.extract).

Uso: python3 sidecar/tests/test_extraction_both_providers_fail.py
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


class TestExtractionBothProvidersFail(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_ambos_con_error_transitorio_termina_en_needs_review(self):
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.RateLimitError("429 insufficient_quota")],
            claude_behavior=[fake.RateLimitError("529 overloaded")],
        )
        result = self.tasks.create_task("both-fail-1", "marianela", None, "algo")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "needs_review")
        self.assertIn("insufficient_quota", final["error_message"])
        self.assertEqual(openai_client.call_count, 1)
        self.assertEqual(claude_client.call_count, 1)

    def test_auth_error_en_primario_no_intenta_el_alternativo(self):
        """'autenticación/configuración inválida: error visible, sin bucle' --
        a propósito NO se prueba el alternativo (ver docstring de
        ai_router.extract): un error de auth se quiere visible y accionable,
        no enmascarado por un fallback silencioso."""
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.AuthenticationError("invalid api key")],
            claude_behavior=[fake.valid_extraction()],  # nunca debería tocarse
        )
        result = self.tasks.create_task("both-fail-2", "marianela", None, "algo")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "failed")
        self.assertIn("Ajustes", final["error_message"])
        self.assertEqual(openai_client.call_count, 1)
        self.assertEqual(claude_client.call_count, 0, "el alternativo no debería haberse llamado")

    def test_ningun_proveedor_configurado_es_auth_error_sin_gastar_llamadas(self):
        fake.install_fake_clients(self.ai_router, openai_behavior=None, claude_behavior=None)
        result = self.tasks.create_task("both-fail-3", "marianela", None, "algo")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "failed")
        self.assertIn("Falta configurar", final["error_message"])

    def test_no_queda_en_parsing_bajo_ninguna_combinacion(self):
        combinaciones = [
            (fake.RateLimitError("x"), fake.AuthenticationError("y")),
            ("malformed", fake.RateLimitError("x")),
            (fake.AuthenticationError("x"), None),
        ]
        for i, (openai_beh, claude_beh) in enumerate(combinaciones):
            fake.install_fake_clients(
                self.ai_router,
                openai_behavior=[openai_beh] if openai_beh is not None else None,
                claude_behavior=[claude_beh] if claude_beh is not None else [fake.valid_extraction()],
            )
            result = self.tasks.create_task(f"no-parsing-{i}", "marianela", None, "algo")
            task = result["task"]
            self.worker._process_classification(task)
            final = self.tasks.get_task_dict(task["task_id"])
            self.assertNotEqual(final["state"], "parsing", f"combinación {i} quedó en parsing")
            self.assertIn(final["state"], ("failed", "needs_review", "ready", "pending_approval", "needs_information"))


if __name__ == "__main__":
    unittest.main()
