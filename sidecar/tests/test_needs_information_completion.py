"""
v0.2.1-rc6, test 7/8 pedido: completar una tarea después de aportar la
información faltante (worker.provide_missing_info) -- CERO llamados nuevos a
un proveedor de IA, reevaluación 100% determinística con lo que ya se había
extraído más lo que aporta Nicolás. Cubre también el caso "todavía no
alcanza" (se queda en needs_information con un evento nuevo, nunca en
silencio).

Uso: python3 sidecar/tests/test_needs_information_completion.py
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


class TestNeedsInformationCompletion(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _create_ambiguous_task(self, key="needs-info-1"):
        _, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.valid_extraction(domain="unknown", intent="register_expense",
                                                     amount=8000, date="18/07/2026", concept="Insumos")],
        )
        result = self.tasks.create_task(key, "marianela", None, "Gasté $8.000 en insumos, no sé de qué área")
        task = result["task"]
        self.worker._process_classification(task)
        return task["task_id"], openai_client

    def test_completar_dominio_reclasifica_sin_llamar_a_ningun_proveedor(self):
        task_id, openai_client = self._create_ambiguous_task()
        needs_info = self.tasks.get_task_dict(task_id)
        self.assertEqual(needs_info["state"], "needs_information")
        calls_before = openai_client.call_count

        final = self.worker.provide_missing_info(task_id, "nicolas", {"domain": "abate"})

        self.assertEqual(final["state"], "ready")  # register_expense con todos los datos -> simple
        self.assertEqual(final["domain"], "abate")
        self.assertEqual(openai_client.call_count, calls_before, "provide_missing_info no debería llamar a ningún proveedor")

    def test_completar_con_dominio_todavia_ambiguo_se_queda_en_needs_information(self):
        task_id, _ = self._create_ambiguous_task(key="needs-info-2")
        result = self.worker.provide_missing_info(task_id, "nicolas", {"domain": "unknown"})

        self.assertEqual(result["state"], "needs_information")
        events = self.tasks.get_task_events(task_id)
        # tiene que quedar un evento NUEVO -- no en silencio, aunque el estado no cambió.
        needs_info_events = [e for e in events if e["to_state"] == "needs_information"]
        self.assertGreaterEqual(len(needs_info_events), 2, "tiene que haber un evento nuevo por el intento de aclaración")

    def test_completar_con_combinacion_no_preparable_termina_en_needs_review_no_en_classified(self):
        """Hallazgo del smoke test post-fix (18/7/2026): completar el dominio
        de una tarea con intent="other" (ej. un mensaje realmente fuera de
        alcance, tipo trading bot) hace que prepare_action() rechace la
        combinación (UnsupportedDomain) -- sin este resguardo, la tarea
        quedaba parada en 'classified' sin ninguna transición final."""
        _, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.valid_extraction(domain="unknown", intent="other",
                                                     amount=None, date=None, concept=None,
                                                     evidence="mención de trading, fuera de alcance")],
        )
        result = self.tasks.create_task("needs-info-4", "marianela", None, "Cambié la posición del bot en BTC")
        task = result["task"]
        self.worker._process_classification(task)
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "needs_information")

        final = self.worker.provide_missing_info(task["task_id"], "nicolas", {"domain": "cfo"})

        self.assertEqual(final["state"], "needs_review")
        self.assertNotEqual(final["state"], "classified", "no debería quedar parada a mitad de camino")
        self.assertIn("preparar la acción", final["error_message"])

    def test_provide_missing_info_solo_aplica_a_needs_information(self):
        result = self.tasks.create_task("needs-info-3", "marianela", None, "algo")
        task_id = result["task"]["task_id"]  # sigue en 'received'
        with self.assertRaises(self.tasks.InvalidTransition):
            self.worker.provide_missing_info(task_id, "nicolas", {"domain": "cfo"})

    def test_completar_tarea_inexistente_da_valueerror(self):
        with self.assertRaises(ValueError):
            self.worker.provide_missing_info("no-existe", "nicolas", {"domain": "cfo"})


if __name__ == "__main__":
    unittest.main()
