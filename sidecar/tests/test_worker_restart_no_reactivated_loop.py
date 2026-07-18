"""
v0.2.1-rc6, test 6/8 pedido: reiniciar el worker (simulado con un WORKER_ID
nuevo sobre la misma base, ya que reimportar el módulo con un WORKER_ID nuevo
ES lo que hace un reinicio real del proceso) no debe reactivar un loop de
reintentos indefinido -- `extraction_attempts` persiste en la base, así que
una tarea que ya gastó su presupuesto antes del reinicio se corta de nuevo
enseguida, sin gastar más llamadas.

Uso: python3 sidecar/tests/test_worker_restart_no_reactivated_loop.py
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


class TestWorkerRestartNoReactivatedLoop(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_reinicio_del_worker_respeta_extraction_attempts_persistido(self):
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            openai_behavior=[fake.RateLimitError("429")] * 5,
            claude_behavior=[fake.RateLimitError("529")] * 5,
        )
        result = self.tasks.create_task("restart-1", "marianela", None, "algo")
        task = result["task"]

        # "Proceso 1" agota el presupuesto (2 intentos).
        self.worker._process_classification(task)
        self.db.get_connection().execute(
            "UPDATE tasks SET state = 'received' WHERE task_id = ?", (task["task_id"],)
        )
        self.db.get_connection().commit()
        task_2 = self.tasks.get_task_dict(task["task_id"])
        self.worker._process_classification(task_2)

        after_process_1 = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(after_process_1["extraction_attempts"], 2)
        self.assertEqual(openai_client.call_count, 2)

        old_worker_id = self.worker.WORKER_ID

        # "Reinicio del proceso" -- se reimporta worker.py, generando un
        # WORKER_ID nuevo (ver worker.py, línea del uuid). recover_orphaned_tasks()
        # corre como lo haría un arranque real.
        sys.modules.pop("worker", None)
        import worker as worker_restarted
        self.assertNotEqual(worker_restarted.WORKER_ID, old_worker_id, "debería ser un WORKER_ID distinto -- simula un proceso nuevo")
        worker_restarted.recover_orphaned_tasks()

        # Si algo (bug hipotético, o una recuperación mal hecha) volviera a
        # dejar la tarea en 'received'/'parsing' tras el "reinicio", el nuevo
        # proceso NO debe reactivar el conteo de intentos desde cero.
        self.db.get_connection().execute(
            "UPDATE tasks SET state = 'received' WHERE task_id = ?", (task["task_id"],)
        )
        self.db.get_connection().commit()
        task_3 = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(task_3["extraction_attempts"], 2, "el contador tiene que sobrevivir al reinicio")

        worker_restarted._process_classification(task_3)
        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "needs_review")
        self.assertIn("límite", final["error_message"].lower())
        # sin gastar ninguna llamada más -- el "reinicio" no resetea el presupuesto.
        self.assertEqual(openai_client.call_count, 2, "el worker reiniciado no debería haber llamado de nuevo")
        self.assertEqual(claude_client.call_count, 2)

    def test_recover_orphaned_tasks_no_llama_a_ningun_proveedor(self):
        """recover_orphaned_tasks() solo debe limpiar locks y loguear -- nunca
        clasificar nada por su cuenta (eso lo hace el loop normal, que sí
        respeta el presupuesto)."""
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router, openai_behavior=[fake.valid_extraction()], claude_behavior=[fake.valid_extraction()],
        )
        self.tasks.create_task("restart-2", "marianela", None, "algo que quedó en parsing")
        # la deja en 'parsing' a propósito, simulando una interrupción real.
        conn = self.db.get_connection()
        conn.execute("UPDATE tasks SET state = 'parsing' WHERE idempotency_key = ?", ("restart-2",))
        conn.commit()

        self.worker.recover_orphaned_tasks()

        self.assertEqual(openai_client.call_count, 0)
        self.assertEqual(claude_client.call_count, 0)


if __name__ == "__main__":
    unittest.main()
