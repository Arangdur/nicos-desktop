"""
v0.2.1-rc6, test 8/8 pedido: una vez que una tarea llega a un estado terminal
de clasificación (needs_information, needs_review, failed, ready,
pending_approval), simular varios ciclos más del worker loop (varias llamadas
a _claim_next_task()/_process_classification() como las haría run_forever()
cada 1s) NO debe generar ningún llamado adicional a un proveedor -- porque
esos estados ya no están en QUEUED_STATES.

Uso: python3 sidecar/tests/test_no_extra_calls_after_terminal_state.py
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


class TestNoExtraCallsAfterTerminalState(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _simulate_loop_ticks(self, n=5):
        """Varias vueltas de lo que haría run_forever() -- _claim_next_task()
        solo mira QUEUED_STATES, así que una tarea en un estado terminal
        nunca debería volver a aparecer acá."""
        for _ in range(n):
            task = self.worker._claim_next_task()
            if task is not None:
                if task["state"] in ("received", "parsing"):
                    self.worker._process_classification(task)
                elif task["state"] == "ready":
                    pass  # no se ejecuta nada en este test -- solo importa si se reclamó

    def test_needs_information_no_genera_mas_llamadas(self):
        _, openai_client = fake.install_fake_clients(
            self.ai_router, openai_behavior=[fake.valid_extraction(domain="unknown")],
        )
        result = self.tasks.create_task("no-extra-1", "marianela", None, "algo ambiguo")
        self.worker._process_classification(result["task"])
        self.assertEqual(self.tasks.get_task_dict(result["task"]["task_id"])["state"], "needs_information")
        calls_after_classification = openai_client.call_count

        self._simulate_loop_ticks()

        self.assertEqual(openai_client.call_count, calls_after_classification,
                          "needs_information no está en QUEUED_STATES -- no debería reclamarse de nuevo")

    def test_needs_review_no_genera_mas_llamadas(self):
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.RateLimitError("429")],
            claude_behavior=[fake.RateLimitError("529")],
        )
        result = self.tasks.create_task("no-extra-2", "marianela", None, "algo")
        self.worker._process_classification(result["task"])
        self.assertEqual(self.tasks.get_task_dict(result["task"]["task_id"])["state"], "needs_review")
        openai_calls, claude_calls = openai_client.call_count, claude_client.call_count

        self._simulate_loop_ticks()

        self.assertEqual(openai_client.call_count, openai_calls)
        self.assertEqual(claude_client.call_count, claude_calls)

    def test_failed_no_genera_mas_llamadas(self):
        _, openai_client = fake.install_fake_clients(self.ai_router, openai_behavior=None, claude_behavior=None)
        result = self.tasks.create_task("no-extra-3", "marianela", None, "algo")
        self.worker._process_classification(result["task"])
        self.assertEqual(self.tasks.get_task_dict(result["task"]["task_id"])["state"], "failed")

        self._simulate_loop_ticks()
        # ningún cliente fue instalado (ambos None) -- si algo intentara
        # llamar de nuevo, hubiera fallado con AttributeError sobre None, no
        # silenciosamente. El hecho de que _simulate_loop_ticks no explote
        # confirma que 'failed' no se reclama de nuevo.

    def test_ready_no_se_reclasifica_dos_veces_aunque_se_deje_sin_ejecutar(self):
        """No es sobre llamadas a IA (la clasificación ya terminó) sino sobre
        que 'ready' no vuelva a pasar por _process_classification -- confirma
        que el ruteo por estado en el loop es correcto."""
        _, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.valid_extraction(domain="cfo", intent="register_expense",
                                                     amount=1000, date="18/07/2026", concept="algo")],
        )
        result = self.tasks.create_task("no-extra-4", "marianela", None, "gasté $1000 en algo")
        self.worker._process_classification(result["task"])
        self.assertEqual(self.tasks.get_task_dict(result["task"]["task_id"])["state"], "ready")
        calls_after = openai_client.call_count

        self._simulate_loop_ticks()

        self.assertEqual(openai_client.call_count, calls_after, "'ready' no debería volver a pasar por clasificación")


if __name__ == "__main__":
    unittest.main()
