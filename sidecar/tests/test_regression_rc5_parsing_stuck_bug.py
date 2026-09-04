"""
v0.2.1-rc6, requisito 8: prueba de regresión explícita. Revierte a propósito
`tasks.ALLOWED_TRANSITIONS['parsing']` al conjunto de rc1-rc5 (sin
'needs_information' ni 'needs_review') y confirma que el bug original del
18/7/2026 vuelve a reproducirse tal cual se documentó: una tarea con dominio
ambiguo queda atascada en 'parsing' para siempre, y el worker la reintenta en
cada ciclo -- quemando una llamada real a un proveedor por ciclo, sin límite.

Si en el futuro alguien revierte (a mano o por un merge accidentado) el
arreglo de la máquina de estados en tasks.py, ESTE archivo tiene que empezar
a fallar solo -- eso es justamente lo que prueba
`test_con_la_maquina_de_estados_actual_NO_se_reproduce_el_bug`.

Uso: python3 sidecar/tests/test_regression_rc5_parsing_stuck_bug.py
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


# Estado EXACTO de ALLOWED_TRANSITIONS['parsing'] en v0.2.1-rc1 a rc5, antes
# de este fix -- ver el diff de tasks.py en el commit de rc6 para confirmar
# que coincide con lo que había.
_OLD_PARSING_TRANSITIONS = {"classified", "failed", "cancelled"}


class TestRegressionRc5ParsingStuckBug(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)
        self._current_parsing_transitions = set(self.tasks.ALLOWED_TRANSITIONS["parsing"])

    def tearDown(self):
        self.tasks.ALLOWED_TRANSITIONS["parsing"] = self._current_parsing_transitions
        os.unlink(self.tmp_db.name)

    def test_revertir_allowed_transitions_reproduce_el_bug_original(self):
        self.tasks.ALLOWED_TRANSITIONS["parsing"] = set(_OLD_PARSING_TRANSITIONS)

        fake.install_fake_clients(
            self.ai_router, claude_behavior=[fake.valid_extraction(domain="unknown")] * 10,
        )
        result = self.tasks.create_task("regression-1", "marianela", None, "algo ambiguo")
        task = result["task"]

        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(
            final["state"], "parsing",
            "con la máquina de estados de rc1-rc5, la tarea debería quedar atascada en 'parsing' "
            "(si esto falla, algo MÁS además de ALLOWED_TRANSITIONS está evitando el bug -- revisar)",
        )

    def test_revertir_allowed_transitions_reproduce_el_loop_de_reintentos_infinito(self):
        """No solo queda atascada una vez -- cada ciclo del worker (como lo
        simula _simulate_loop_ticks) vuelve a gastar una llamada real al
        proveedor, sin límite. Esto es lo que de verdad pasó en producción el
        18/7/2026 antes de cortarlo a mano."""
        self.tasks.ALLOWED_TRANSITIONS["parsing"] = set(_OLD_PARSING_TRANSITIONS)

        claude_client, _ = fake.install_fake_clients(
            self.ai_router, claude_behavior=[fake.valid_extraction(domain="unknown")] * 10,
        )
        result = self.tasks.create_task("regression-2", "marianela", None, "algo ambiguo")
        task = result["task"]

        for tick in range(5):
            current = self.tasks.get_task_dict(task["task_id"])
            self.assertIn(current["state"], ("received", "parsing"),
                          f"tick {tick}: se esperaba que siguiera atascada, salió a '{current['state']}'")
            self.worker._process_classification(current)

        # 5 ciclos, 5 llamadas reales -- sin ningún límite, exactamente el
        # comportamiento que MAX_EXTRACTION_ATTEMPTS (con la máquina de
        # estados arreglada) corta en 2.
        self.assertEqual(claude_client.call_count, 5)
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "parsing")

    def test_con_la_maquina_de_estados_actual_NO_se_reproduce_el_bug(self):
        """Control positivo -- confirma que, SIN revertir nada (el estado
        real del código en este commit), el mismo escenario exacto sale de
        'parsing' en el primer intento. Si este test empieza a fallar sin que
        el de arriba haya cambiado, el fix se rompió de otra forma."""
        claude_client, _ = fake.install_fake_clients(
            self.ai_router, claude_behavior=[fake.valid_extraction(domain="unknown")] * 10,
        )
        result = self.tasks.create_task("regression-3", "marianela", None, "algo ambiguo")
        task = result["task"]

        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "needs_information")
        self.assertEqual(claude_client.call_count, 1)


if __name__ == "__main__":
    unittest.main()
