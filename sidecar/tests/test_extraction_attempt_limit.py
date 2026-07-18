"""
v0.2.1-rc6, requisito 4: `extraction_attempts` + `MAX_EXTRACTION_ATTEMPTS` --
ninguna tarea puede generar llamadas ilimitadas a proveedores, sin importar
qué combinación de fallas ocurra. Esta es la segunda red de seguridad,
independiente del arreglo de la máquina de estados (ver tasks.py): si por
algún bug futuro una tarea volviera a 'received'/'parsing' repetidas veces,
este límite la corta ANTES de llamar a ningún proveedor.

Uso: python3 sidecar/tests/test_extraction_attempt_limit.py
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


class TestExtractionAttemptLimit(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_extraction_attempts_se_incrementa_y_persiste(self):
        fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.RateLimitError("429")],
            claude_behavior=[fake.RateLimitError("529")],
        )
        result = self.tasks.create_task("attempt-limit-1", "marianela", None, "algo")
        task = result["task"]
        self.assertEqual(task.get("extraction_attempts") or 0, 0)

        self.worker._process_classification(task)
        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["extraction_attempts"], 1)

    def test_forzar_una_tarea_de_vuelta_a_received_respeta_el_limite_sin_llamar_proveedores(self):
        """Simula el escenario que MAX_EXTRACTION_ATTEMPTS existe para cubrir:
        una tarea que, por la razón que sea, vuelve a pasar por
        _process_classification() más veces de las que el presupuesto
        permite. El respaldo tiene que cortarla ANTES de gastar ninguna
        llamada más, incluso si algo (hipotéticamente) la devolviera a
        'received' otra vez."""
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.RateLimitError("429")] * 5,
            claude_behavior=[fake.RateLimitError("529")] * 5,
        )
        result = self.tasks.create_task("attempt-limit-2", "marianela", None, "algo")
        task = result["task"]

        # Intento 1: ambos proveedores fallan -> needs_review, extraction_attempts=1.
        self.worker._process_classification(task)
        after_1 = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(after_1["state"], "needs_review")
        self.assertEqual(after_1["extraction_attempts"], 1)
        self.assertEqual(openai_client.call_count, 1)
        self.assertEqual(claude_client.call_count, 1)

        # Se fuerza de vuelta a 'received' a mano -- simula un bug hipotético
        # o una recuperación manual mal hecha -- para probar que el límite
        # sigue aplicando aunque el estado se manipule por fuera del flujo normal.
        self.db.get_connection().execute(
            "UPDATE tasks SET state = 'received' WHERE task_id = ?", (task["task_id"],)
        )
        self.db.get_connection().commit()
        task_again = self.tasks.get_task_dict(task["task_id"])

        # Intento 2 (MAX_EXTRACTION_ATTEMPTS = 2): todavía se permite, gasta llamadas.
        self.worker._process_classification(task_again)
        after_2 = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(after_2["extraction_attempts"], 2)
        self.assertEqual(openai_client.call_count, 2)
        self.assertEqual(claude_client.call_count, 2)

        # Se fuerza una tercera vuelta -- esta vez el límite tiene que cortar
        # ANTES de llamar a ningún proveedor.
        self.db.get_connection().execute(
            "UPDATE tasks SET state = 'received' WHERE task_id = ?", (task["task_id"],)
        )
        self.db.get_connection().commit()
        task_third = self.tasks.get_task_dict(task["task_id"])
        self.worker._process_classification(task_third)

        after_3 = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(after_3["state"], "needs_review")
        self.assertIn("límite", after_3["error_message"].lower())
        # sin gastar NINGUNA llamada extra -- el corte es antes de tocar ai_router.
        self.assertEqual(openai_client.call_count, 2, "no debería haber llamado de nuevo a OpenAI")
        self.assertEqual(claude_client.call_count, 2, "no debería haber llamado de nuevo a Claude")


if __name__ == "__main__":
    unittest.main()
