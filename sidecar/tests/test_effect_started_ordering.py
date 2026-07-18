"""
v0.2.1-rc2 -- confirma explícitamente que 'effect_started' se persiste
(INSERT/UPDATE + commit) en execution_attempts ANTES de que se invoque
subprocess.run(). Si el orden fuera al revés, existiría una ventana donde el
subprocess ya está corriendo (y podría llegar a escribir el movimiento real)
pero NicOS Core todavía cree que el intento sigue en 'claimed' -- y
reconcile_execution_attempt() trataría un 'claimed' como certeza de que nunca
se invocó nada, lo cual sería falso.

Se prueba interceptando subprocess.run con un espía que consulta la base de
datos ANTES de simular la ejecución -- si execute_action() invirtiera el
orden, el espía vería 'claimed' en vez de 'effect_started' y el test fallaría.

Uso: python3 sidecar/tests/test_effect_started_ordering.py
"""
import os
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import centro_mando_adapter as cma_mod
    return db_mod, tasks_mod, cma_mod


class FakeCompletedProcess:
    def __init__(self, returncode=0, stdout="OK: agregado a foto_financiera_test.md, sección 6 (Movimientos del mes)", stderr=""):
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = stderr


class TestEffectStartedOrdering(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.cma = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_effect_started_persistido_antes_de_invocar_subprocess(self):
        result = self.tasks.create_task("ordering-test-1", "nicolas", None, "Gasté $5.000 en algo")
        task_id = result["task"]["task_id"]

        observado = {}

        def _spy_subprocess_run(cmd, **kwargs):
            # Consulta la base DIRECTAMENTE (conexión propia, no cacheada) para
            # confirmar que el commit de 'effect_started' ya se ve reflejado en
            # disco en el momento exacto en que se invocaría el subprocess real.
            conn = self.db.get_connection()
            row = conn.execute(
                "SELECT status FROM execution_attempts WHERE task_id = ? ORDER BY started_at DESC LIMIT 1",
                (task_id,),
            ).fetchone()
            observado["status_al_invocar_subprocess"] = row["status"] if row else None
            return FakeCompletedProcess()

        with patch("centro_mando_adapter.subprocess.run", side_effect=_spy_subprocess_run):
            prepared = {"domain": "cfo", "args": {"fecha": "2026-07-18", "concepto": "algo", "monto": 5000, "tipo": "gasto", "detalle": ""}}
            self.cma.execute_action(task_id, prepared)

        self.assertEqual(
            observado.get("status_al_invocar_subprocess"), "effect_started",
            "el estado tiene que ser 'effect_started' EN EL MOMENTO en que se invoca subprocess.run, "
            "no 'claimed' (que indicaría que el cambio de estado ocurre después, dejando una ventana ciega)",
        )

    def test_execution_attempt_arranca_en_claimed_antes_de_execute_action(self):
        # Contraste: create_execution_attempt() por sí solo (sin invocar
        # execute_action) deja el estado en 'claimed' -- confirma que el valor
        # inicial es el esperado antes de que exista ninguna ventana de duda.
        result = self.tasks.create_task("ordering-test-2", "nicolas", None, "Gasté $5.000 en otra cosa")
        task_id = result["task"]["task_id"]
        attempt = self.tasks.create_execution_attempt(task_id, "op-ordering-2", executor="test")
        self.assertEqual(attempt["status"], "claimed")
        row = self.db.get_connection().execute(
            "SELECT status FROM execution_attempts WHERE execution_id = ?", (attempt["execution_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "claimed")


if __name__ == "__main__":
    unittest.main()
