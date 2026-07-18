"""
Punto 1-2 de la revisión post-v0.2.1, endurecido en rc2: el operation_id tiene
que quedar asociado de forma durable a un recibo verificable -- no solo a la
sola PRESENCIA en un ledger de una fase (eso fue el gap real señalado en la
revisión de rc1: 'reserved' no demuestra que se haya escrito). Este archivo
prueba reconcile_execution_attempt() en aislamiento, con el ledger de dos
fases (reserved/committed/failed) escrito directamente en formato JSONL, sin
pasar por el registrar_movimiento.py real -- eso vive en
test_reconciliation_gap_v2.py, que sí usa el script real y cubre los 3
escenarios exactos pedidos en la revisión.

Uso: python3 sidecar/tests/test_execution_reconciliation.py
"""
import json
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path, tmp_ledger_path, tmp_cfo_dir):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    os.environ["NICOS_OPERATION_LEDGER"] = tmp_ledger_path
    os.environ["NICOS_CFO_DIR"] = tmp_cfo_dir
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    import centro_mando_adapter as cma_mod
    return db_mod, tasks_mod, worker_mod, cma_mod


class TestExecutionReconciliation(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.tmp_dir = tempfile.mkdtemp(prefix="nicos-reconcile-")
        self.tmp_ledger = os.path.join(self.tmp_dir, "OPERATION_IDS_PROCESADOS.txt")
        self.tmp_cfo_dir = os.path.join(self.tmp_dir, "cfo")
        os.makedirs(self.tmp_cfo_dir)
        self.tmp_foto = os.path.join(self.tmp_cfo_dir, "foto_financiera_testmes.md")
        with open(self.tmp_foto, "w", encoding="utf-8") as f:
            f.write("## 6. MOVIMIENTOS DEL MES\n\n| Fecha | Concepto | Monto | Tipo |\n|---|---|---|---|\n\n---\n")
        self.db, self.tasks, self.worker, self.cma = _fresh_modules(self.tmp_db.name, self.tmp_ledger, self.tmp_cfo_dir)

    def tearDown(self):
        os.unlink(self.tmp_db.name)
        import shutil
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def _write_ledger_entry(self, operation_id, status, row_text=None):
        entry = {"operation_id": operation_id, "status": status, "ts": "2026-07-18T00:00:00"}
        if row_text is not None:
            entry["row_text"] = row_text
        with open(self.tmp_ledger, "a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")

    def _write_row_to_foto(self, row_text):
        with open(self.tmp_foto, "r", encoding="utf-8") as f:
            contenido = f.read()
        idx = contenido.find("\n---")
        with open(self.tmp_foto, "w", encoding="utf-8") as f:
            f.write(contenido[:idx] + row_text + "\n" + contenido[idx:])

    # ---- reconcile_execution_attempt() en aislamiento ----

    def test_claimed_siempre_effect_failed_sin_mirar_el_ledger(self):
        self._write_ledger_entry("op-fantasma", "committed", row_text="| x |")
        attempt = {"status": "claimed", "operation_id": "op-nunca-invocado"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_failed")

    def test_committed_con_evidencia_verificable_es_confirmado(self):
        row_text = "| 18/07/2026 | Test reconcile | $1.000 | Gasto variable |"
        self._write_ledger_entry("op-abc-123", "reserved", row_text=row_text)
        self._write_row_to_foto(row_text)
        self._write_ledger_entry("op-abc-123", "committed", row_text=row_text)
        attempt = {"status": "effect_started", "operation_id": "op-abc-123"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_confirmed")

    def test_committed_SIN_evidencia_en_el_archivo_es_uncertain(self):
        """El corazón del endurecimiento de rc2: la palabra 'committed' del
        ledger NUNCA se toma como suficiente por sí sola -- si el texto no
        aparece en el archivo real (inconsistencia, archivo editado después,
        etc.), la reconciliación desconfía y devuelve 'uncertain', no 'effect_confirmed'."""
        row_text = "| 18/07/2026 | Este texto no está en el archivo | $1.000 | Gasto variable |"
        self._write_ledger_entry("op-committed-sin-evidencia", "committed", row_text=row_text)
        attempt = {"status": "effect_started", "operation_id": "op-committed-sin-evidencia"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "uncertain")

    def test_reserved_solo_sin_evidencia_es_uncertain_no_effect_failed(self):
        # Antes (rc1) esto se interpretaba como certeza de fallo. Corregido:
        # 'reserved' es ambiguo -- podría haberse escrito y el proceso morir
        # antes del marcador 'committed'. Sin evidencia en el archivo, la
        # respuesta correcta es 'uncertain', nunca 'effect_failed'.
        row_text = "| 18/07/2026 | Nunca se escribió | $1.000 | Gasto variable |"
        self._write_ledger_entry("op-solo-reservado", "reserved", row_text=row_text)
        attempt = {"status": "effect_started", "operation_id": "op-solo-reservado"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "uncertain")

    def test_reserved_con_evidencia_real_es_confirmado(self):
        # El caso que motivó el endurecimiento: 'reserved' nada más, PERO el
        # archivo real sí tiene la fila (el proceso escribió y murió antes de
        # marcar 'committed') -- se reconcilia a effect_confirmed igual, por
        # evidencia independiente, no por la palabra del ledger.
        row_text = "| 18/07/2026 | Se escribió pero no se marcó committed | $1.000 | Gasto variable |"
        self._write_ledger_entry("op-reserved-pero-escrito", "reserved", row_text=row_text)
        self._write_row_to_foto(row_text)
        attempt = {"status": "effect_started", "operation_id": "op-reserved-pero-escrito"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_confirmed")

    def test_failed_explicito_en_el_ledger_es_effect_failed(self):
        # Certeza real: el script mismo detectó limpiamente que no podía
        # escribir (ej. no existe el archivo de destino) y lo registró.
        self._write_ledger_entry("op-fallo-detectado", "failed")
        attempt = {"status": "effect_started", "operation_id": "op-fallo-detectado"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "effect_failed")

    def test_ledger_sin_ninguna_entrada_para_este_operation_id_es_uncertain(self):
        attempt = {"status": "effect_started", "operation_id": "op-que-no-existe-en-el-ledger"}
        self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "uncertain")

    def test_ledger_illegible_es_uncertain(self):
        os.makedirs(self.tmp_ledger)
        try:
            attempt = {"status": "effect_started", "operation_id": "op-cualquiera"}
            self.assertEqual(self.cma.reconcile_execution_attempt(attempt), "uncertain")
        finally:
            os.rmdir(self.tmp_ledger)

    # ---- el escenario real que motivó el punto 1, vía worker.recover_orphaned_tasks ----

    def _make_task_stuck_executing(self, operation_id):
        result = self.tasks.create_task(f"reconcile-test-{operation_id}", "nicolas", None, "Gasté $10.000 en algo")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "command": "registrar_movimiento.py",
                    "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 10000, "tipo": "gasto", "detalle": ""}}
        action_hash = self.tasks.compute_action_hash(prepared)
        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="simple", task_revision=1)
        self.tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})
        self.tasks.transition(task_id, "executing", "system")
        attempt = self.tasks.create_execution_attempt(task_id, operation_id, executor="centro_mando_adapter")
        self.tasks.update_execution_attempt_status(attempt["execution_id"], "effect_started")
        return task_id, attempt

    def test_efecto_SI_ocurrio_pero_nicos_murio_antes_de_registrar_resultado(self):
        task_id, attempt = self._make_task_stuck_executing("op-realmente-ejecutado")
        row_text = "| 17/07/2026 | algo | $10.000 | Gasto variable |"
        self._write_ledger_entry("op-realmente-ejecutado", "committed", row_text=row_text)
        self._write_row_to_foto(row_text)

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "completed", "debería auto-reconciliarse a completed, no quedar en needs_review")

        events = self.tasks.get_task_events(task_id)
        detail = json.loads(events[-1]["detail_json"])
        self.assertEqual(detail.get("reconciliacion"), "effect_confirmed")

    def test_efecto_NO_ocurrio_queda_en_needs_review_como_uncertain(self):
        task_id, attempt = self._make_task_stuck_executing("op-nunca-llego-a-escribir")
        # Nada en el ledger para este operation_id.

        self.worker.recover_orphaned_tasks()

        task = self.tasks.get_task_dict(task_id)
        self.assertEqual(task["state"], "needs_review")

        events = self.tasks.get_task_events(task_id)
        detail = json.loads(events[-1]["detail_json"])
        self.assertEqual(detail.get("reconciliacion"), "uncertain")


if __name__ == "__main__":
    unittest.main()
