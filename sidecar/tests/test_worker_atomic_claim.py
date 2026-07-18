"""
Punto 5 de la revisión post-v0.2.1: dos procesos nunca pueden ejecutar la
misma tarea a la vez. Simula dos "workers" (dos WORKER_ID distintos, en el
mismo proceso de test) compitiendo por la misma tarea 'ready' -- solo uno debe
ganar el UPDATE atómico.

Uso: python3 sidecar/tests/test_worker_atomic_claim.py
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


class TestWorkerAtomicClaim(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _make_ready_task(self, key):
        result = self.tasks.create_task(key, "nicolas", None, "Gasté $10.000 en algo")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 10000, "tipo": "gasto"}}
        action_hash = self.tasks.compute_action_hash(prepared)
        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="simple", task_revision=1)
        self.tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})
        return task_id

    def test_dos_workers_compitiendo_solo_uno_gana(self):
        task_id = self._make_ready_task("atomic-claim-1")

        # Dos identidades de worker distintas, simulando dos procesos.
        worker_a_id = "worker-A-simulado"
        worker_b_id = "worker-B-simulado"

        conn = self.db.get_connection()
        cur_a = conn.execute(
            "UPDATE tasks SET locked_by = ?, locked_at = ? WHERE task_id = ? AND state = ? AND (locked_by IS NULL OR locked_by = ?)",
            (worker_a_id, "2026-07-18T00:00:00", task_id, "ready", worker_a_id),
        )
        conn.commit()
        cur_b = conn.execute(
            "UPDATE tasks SET locked_by = ?, locked_at = ? WHERE task_id = ? AND state = ? AND (locked_by IS NULL OR locked_by = ?)",
            (worker_b_id, "2026-07-18T00:00:01", task_id, "ready", worker_b_id),
        )
        conn.commit()

        self.assertEqual(cur_a.rowcount, 1, "worker A debería haber ganado el claim (llegó primero)")
        self.assertEqual(cur_b.rowcount, 0, "worker B NO debería poder reclamar una tarea ya tomada por A")

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["locked_by"], worker_a_id)

    def test_mismo_worker_puede_reclamar_su_propia_tarea_dos_veces(self):
        # No debería fallar si el mismo worker (mismo WORKER_ID) vuelve a
        # tocar una tarea que ya se auto-asignó -- la condición (locked_by IS
        # NULL OR locked_by = ?) lo permite a propósito.
        task_id = self._make_ready_task("atomic-claim-2")
        conn = self.db.get_connection()
        worker_id = "worker-unico"
        for _ in range(2):
            cur = conn.execute(
                "UPDATE tasks SET locked_by = ?, locked_at = ? WHERE task_id = ? AND state = ? AND (locked_by IS NULL OR locked_by = ?)",
                (worker_id, "2026-07-18T00:00:00", task_id, "ready", worker_id),
            )
            conn.commit()
            self.assertEqual(cur.rowcount, 1)

    def test_claim_next_task_real_devuelve_la_tarea_y_la_marca(self):
        task_id = self._make_ready_task("atomic-claim-3")
        claimed = self.worker._claim_next_task()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["task_id"], task_id)
        self.assertEqual(claimed["locked_by"], self.worker.WORKER_ID)

    def test_clear_stale_locks_limpia_locks_de_corrida_anterior(self):
        task_id = self._make_ready_task("atomic-claim-4")
        conn = self.db.get_connection()
        conn.execute("UPDATE tasks SET locked_by = 'proceso-anterior-muerto', locked_at = '2026-07-17T00:00:00' WHERE task_id = ?", (task_id,))
        conn.commit()

        self.worker._clear_stale_locks()

        task = self.tasks.get_task_dict(task_id)
        self.assertIsNone(task["locked_by"], "un lock de una corrida anterior debería limpiarse al arrancar")

        # Y ahora este worker (con su propio WORKER_ID, distinto al de la
        # corrida anterior) debería poder reclamarla sin problema.
        claimed = self.worker._claim_next_task()
        self.assertIsNotNone(claimed)
        self.assertEqual(claimed["task_id"], task_id)


if __name__ == "__main__":
    unittest.main()
