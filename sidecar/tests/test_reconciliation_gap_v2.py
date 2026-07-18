"""
v0.2.1-rc2 — cierra el gap crítico señalado en la revisión: un ledger de UNA
sola fase (donde la sola presencia de operation_id se leía como "ya escrito")
no distingue "reservado, proceso murió antes de escribir" de "escrito de
verdad, proceso murió antes de marcar committed". Ahora registrar_movimiento.py
usa un ledger de DOS fases (reserved -> committed, o failed si se detecta
limpiamente un error) con row_text -- el texto exacto de la fila -- guardado
YA en la reserva, para que NicOS Core pueda verificar independientemente
contra el archivo real sin depender de que el marcador 'committed' se haya
alcanzado a escribir.

Los 3 escenarios exactos pedidos, simulados llamando a las mismas funciones
que usaría el script real (_ledger_write, _build_fila_cfo) en el punto exacto
donde "se mata" el proceso -- sin duplicar lógica de formato (se importa
_build_fila_cfo del script real).

Usa archivos y base temporales -- nunca toca nada real.

Uso: python3 sidecar/tests/test_reconciliation_gap_v2.py
"""
import glob
import importlib.util
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REGISTRAR_MOVIMIENTO_PATH = "/Users/nicolasbuso/Claude/Projects/Centro de Mando/herramientas/registrar_movimiento.py"


def _load_registrar_movimiento():
    spec = importlib.util.spec_from_file_location("registrar_movimiento_bajo_test", REGISTRAR_MOVIMIENTO_PATH)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def _fresh_nicos_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    import centro_mando_adapter as cma_mod
    return db_mod, tasks_mod, worker_mod, cma_mod


class TestReconciliationGapV2(unittest.TestCase):
    def setUp(self):
        self.tmp_root = tempfile.mkdtemp(prefix="nicos-gapv2-")
        self.tmp_cfo_dir = os.path.join(self.tmp_root, "cfo")
        os.makedirs(self.tmp_cfo_dir)
        self.tmp_foto = os.path.join(self.tmp_cfo_dir, "foto_financiera_testmes.md")
        with open(self.tmp_foto, "w", encoding="utf-8") as f:
            f.write("## 6. MOVIMIENTOS DEL MES\n\n| Fecha | Concepto | Monto | Tipo |\n|---|---|---|---|\n\n---\n")

        self.tmp_ledger = os.path.join(self.tmp_root, "OPERATION_IDS_PROCESADOS.txt")
        os.environ["NICOS_CFO_DIR"] = self.tmp_cfo_dir
        os.environ["NICOS_OPERATION_LEDGER"] = self.tmp_ledger

        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.cma = _fresh_nicos_modules(self.tmp_db.name)

        self.rm = _load_registrar_movimiento()  # registrar_movimiento.py real, cargado con las env vars ya seteadas

    def tearDown(self):
        os.unlink(self.tmp_db.name)
        import shutil
        shutil.rmtree(self.tmp_root, ignore_errors=True)

    def _foto_content(self):
        with open(self.tmp_foto, "r", encoding="utf-8") as f:
            return f.read()

    def _make_task_stuck_executing(self, operation_id):
        result = self.tasks.create_task(f"gapv2-{operation_id}", "nicolas", None, "Gasté $7.500 en algo")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "command": "registrar_movimiento.py",
                    "args": {"fecha": "2026-07-18", "concepto": "algo gap v2", "monto": 7500, "tipo": "gasto", "detalle": ""}}
        action_hash = self.tasks.compute_action_hash(prepared)
        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="simple", task_revision=1)
        self.tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})
        self.tasks.transition(task_id, "executing", "system")
        attempt = self.tasks.create_execution_attempt(task_id, operation_id, executor="centro_mando_adapter")
        self.tasks.update_execution_attempt_status(attempt["execution_id"], "effect_started")
        return task_id, attempt, prepared["args"]

    # ---- Escenario 1: kill después de reservar, antes de escribir ----

    def test_escenario_1_kill_tras_reservar_antes_de_escribir(self):
        operation_id = "op-gapv2-escenario-1"
        task_id, attempt, args = self._make_task_stuck_executing(operation_id)

        fila = self.rm._build_fila_cfo(args["fecha"], args["concepto"], args["monto"], args["tipo"], args["detalle"])
        # Solo reserva -- el "proceso" muere ANTES de tocar el archivo real.
        self.rm._ledger_write(operation_id, "reserved", row_text=fila.rstrip("\n"))

        # Ground truth del escenario, confirmado antes de reconciliar:
        self.assertNotIn(args["concepto"], self._foto_content(), "el movimiento debe estar AUSENTE del archivo real")
        self.assertEqual(self.rm._ledger_status(operation_id), "reserved")

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "needs_review", "nunca 'completed' cuando no hay evidencia del efecto")
        self.assertNotEqual(task["state"], "completed")

        row = self.db.get_connection().execute(
            "SELECT status FROM execution_attempts WHERE execution_id = ?", (attempt["execution_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "uncertain", "reserved-solo sin evidencia debe ser 'uncertain', no 'effect_failed'")

    # ---- Escenario 2: kill después de escribir, antes de marcar committed ----

    def test_escenario_2_kill_tras_escribir_antes_de_committed(self):
        operation_id = "op-gapv2-escenario-2"
        task_id, attempt, args = self._make_task_stuck_executing(operation_id)

        fila = self.rm._build_fila_cfo(args["fecha"], args["concepto"], args["monto"], args["tipo"], args["detalle"])
        fila_sin_salto = fila.rstrip("\n")

        # Mismo orden que cmd_cfo real: reservar, escribir el archivo... y ahí
        # el "proceso" muere -- NUNCA se llega a llamar _ledger_write(committed).
        self.rm._ledger_write(operation_id, "reserved", row_text=fila_sin_salto)
        contenido = self._foto_content()
        idx = contenido.find("\n---")
        nuevo_contenido = contenido[:idx] + fila + contenido[idx:]
        with open(self.tmp_foto, "w", encoding="utf-8") as f:
            f.write(nuevo_contenido)
        # (deliberadamente NO se llama _ledger_write(operation_id, "committed", ...))

        # Ground truth: el ledger SOLO tiene 'reserved' (nunca escaló a
        # 'committed'), pero el archivo real SÍ tiene el movimiento.
        self.assertEqual(self.rm._ledger_status(operation_id), "reserved")
        self.assertIn(args["concepto"], self._foto_content())

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "completed",
                          "el efecto SÍ ocurrió y se puede demostrar contra el archivo real -- debe reconciliarse a completed")

        row = self.db.get_connection().execute(
            "SELECT status FROM execution_attempts WHERE execution_id = ?", (attempt["execution_id"],)
        ).fetchone()
        self.assertEqual(row["status"], "effect_confirmed")

    def test_escenario_2b_contraste_sin_evidencia_en_el_archivo_es_uncertain(self):
        # Variante del escenario 2 pero donde la verificación NO encuentra el
        # texto (ej. alguien tocó el archivo después, o el 'reserved' mentía) --
        # confirma que la reconciliación no confía ciegamente, incluso si en
        # teoría "debería" haberse escrito.
        operation_id = "op-gapv2-escenario-2b"
        task_id, attempt, args = self._make_task_stuck_executing(operation_id)
        fila = self.rm._build_fila_cfo(args["fecha"], args["concepto"], args["monto"], args["tipo"], args["detalle"])
        self.rm._ledger_write(operation_id, "reserved", row_text=fila.rstrip("\n"))
        # El archivo real NUNCA se toca -- el texto reservado no aparece ahí.

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "needs_review")

    # ---- Escenario 3: kill después de committed, antes de que NicOS marque completed ----

    def test_escenario_3_kill_tras_committed_antes_de_marcar_completed(self):
        operation_id = "op-gapv2-escenario-3"
        task_id, attempt, args = self._make_task_stuck_executing(operation_id)

        fila = self.rm._build_fila_cfo(args["fecha"], args["concepto"], args["monto"], args["tipo"], args["detalle"])
        fila_sin_salto = fila.rstrip("\n")

        # Flujo completo del lado de registrar_movimiento.py: reservar,
        # escribir, committed. El "proceso" (NicOS Core, no el script) muere
        # DESPUÉS de esto -- nunca llama tasks.finish_execution_attempt() ni
        # tasks.transition(..., "completed", ...).
        self.rm._ledger_write(operation_id, "reserved", row_text=fila_sin_salto)
        contenido = self._foto_content()
        idx = contenido.find("\n---")
        nuevo_contenido = contenido[:idx] + fila + contenido[idx:]
        with open(self.tmp_foto, "w", encoding="utf-8") as f:
            f.write(nuevo_contenido)
        self.rm._ledger_write(operation_id, "committed", row_text=fila_sin_salto)

        self.assertEqual(self.rm._ledger_status(operation_id), "committed")

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "completed", "reconciliación automática a completed")

        # "Ninguna reejecución": confirmamos que no quedó ninguna OTRA fila de
        # execution_attempts para esta tarea (no se generó un operation_id
        # nuevo ni se volvió a invocar nada).
        attempts = self.db.get_connection().execute(
            "SELECT COUNT(*) as n FROM execution_attempts WHERE task_id = ?", (task_id,)
        ).fetchone()
        self.assertEqual(attempts["n"], 1, "no debería haber un segundo intento -- nunca se reejecuta")

        # Y el contenido del archivo real solo tiene UNA fila con este concepto
        # (no se duplicó).
        ocurrencias = self._foto_content().count(args["concepto"])
        self.assertEqual(ocurrencias, 1, "el movimiento no debería estar duplicado")


if __name__ == "__main__":
    unittest.main()
