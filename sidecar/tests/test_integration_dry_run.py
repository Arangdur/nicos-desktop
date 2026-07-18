"""
Etapa 10 — flujo completo (creación de tarea -> worker -> execute_action REAL)
para los 10 movimientos reales del 17/07/2026, contra copias temporales de
TODO lo que se escribe: foto_financiera_*.md, REGISTRO_ENTRADA.md, el ledger de
operation_id, y nicos.db. Nunca toca los archivos reales de Centro de Mando ni
de CFO y Decisiones Estratégicas -- ver test_no_toca_archivos_reales, que lo
verifica activamente (hash antes/después), no solo lo asume.

Lo único que se stubea es ai_router.extract() (la llamada real a Claude/OpenAI):
correr esto contra la API de verdad costaría dinero y sería no-determinístico
en un test automatizado. Todo lo demás -- worker._process_classification(),
worker._process_execution(), centro_mando_adapter.execute_action(), y el
registrar_movimiento.py REAL corriendo por subprocess -- es código de producción
sin mockear, apuntado a rutas temporales via las variables de entorno que ya
soportan CFO_DIR/REGISTRO_ENTRADA/OPERATION_LEDGER/JARVIS_TRABAJO.

Uso: python3 sidecar/tests/test_integration_dry_run.py
"""
import glob
import hashlib
import importlib
import json
import os
import shutil
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

REAL_CFO_DIR = "/Users/nicolasbuso/Claude/Projects/CFO y Decisiones Estrategicas"
REAL_CENTRO_DE_MANDO_DIR = "/Users/nicolasbuso/Claude/Projects/Centro de Mando"
REAL_JARVIS_TRABAJO = "/Users/nicolasbuso/trading_bot/jarvis-trabajo"
REAL_REGISTRO_ENTRADA = os.path.join(REAL_CENTRO_DE_MANDO_DIR, "REGISTRO_ENTRADA.md")
REAL_OPERATION_LEDGER = os.path.join(REAL_CENTRO_DE_MANDO_DIR, "OPERATION_IDS_PROCESADOS.txt")

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "casos_17_07.json")
TIPO_TO_INTENT = {"gasto": "register_expense", "ingreso": "register_income"}

RAW_TEXT_BY_CONCEPT = {
    "Funda de teléfono": "Gasté $35.000 en una funda de teléfono",
    "VR Arena": "Pagué $180.000 en VR Arena",
    "Cena con mi esposa": "Cena con mi esposa, salió $100.000",
    "Starbucks": "Café en Starbucks, $34.400",
    "Resina epoxi": "Compré resina epoxi por $132.000",
    "Brunch Le Blé": "Brunch en Le Blé, $92.400",
    "Extravío de dinero": "Se me perdieron $100.000 en efectivo",
    "Parking": "Parking $19.200",
    "Hospital Posse - consultas psiquiatría": "Cobré $1.755.000 de Hospital Posse por consultas de psiquiatría",
    "Regalo": "Compré un regalo por $300.000",
}


def _file_hash(path):
    if not os.path.exists(path):
        return None
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def _hash_dir_snapshot():
    """Hashea todo lo que este test podría llegar a tocar por error, para
    poder afirmar con evidencia (no solo con la intención del código) que
    nada real cambió."""
    snapshot = {REAL_REGISTRO_ENTRADA: _file_hash(REAL_REGISTRO_ENTRADA),
                REAL_OPERATION_LEDGER: _file_hash(REAL_OPERATION_LEDGER)}
    for path in glob.glob(os.path.join(REAL_CFO_DIR, "foto_financiera_*.md")):
        snapshot[path] = _file_hash(path)
    return snapshot


class TestIntegrationDryRun(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.pre_snapshot = _hash_dir_snapshot()

        cls.tmp_root = tempfile.mkdtemp(prefix="nicos-dry-run-")
        tmp_cfo_dir = os.path.join(cls.tmp_root, "cfo")
        tmp_centro_dir = os.path.join(cls.tmp_root, "centro_de_mando")
        tmp_herramientas_dir = os.path.join(tmp_centro_dir, "herramientas")
        tmp_jarvis_dir = os.path.join(cls.tmp_root, "jarvis")
        os.makedirs(tmp_cfo_dir)
        os.makedirs(tmp_herramientas_dir)
        os.makedirs(tmp_jarvis_dir)

        real_foto = sorted(
            glob.glob(os.path.join(REAL_CFO_DIR, "foto_financiera_*.md")),
            key=os.path.getmtime, reverse=True,
        )[0]
        cls.tmp_foto = os.path.join(tmp_cfo_dir, os.path.basename(real_foto))
        shutil.copyfile(real_foto, cls.tmp_foto)

        shutil.copyfile(
            os.path.join(REAL_CENTRO_DE_MANDO_DIR, "herramientas", "registrar_movimiento.py"),
            os.path.join(tmp_herramientas_dir, "registrar_movimiento.py"),
        )

        cls.tmp_registro_entrada = os.path.join(tmp_centro_dir, "REGISTRO_ENTRADA.md")
        cls.tmp_ledger = os.path.join(tmp_centro_dir, "OPERATION_IDS_PROCESADOS.txt")
        cls.tmp_db = os.path.join(cls.tmp_root, "nicos.db")

        os.environ["NICOS_CFO_DIR"] = tmp_cfo_dir
        os.environ["NICOS_REGISTRO_ENTRADA"] = cls.tmp_registro_entrada
        os.environ["NICOS_OPERATION_LEDGER"] = cls.tmp_ledger
        os.environ["NICOS_JARVIS_TRABAJO"] = tmp_jarvis_dir
        os.environ["CENTRO_DE_MANDO_DIR"] = tmp_centro_dir
        os.environ["NICOS_DB_PATH"] = cls.tmp_db

        for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
            sys.modules.pop(mod, None)

        import db as db_mod
        db_mod.run_migrations()

        import tasks as tasks_mod
        import worker as worker_mod
        import centro_mando_adapter as cma_mod
        import ai_router as ai_router_mod

        cls.db = db_mod
        cls.tasks = tasks_mod
        cls.worker = worker_mod
        cls.cma = cma_mod
        cls.ai_router = ai_router_mod

        assert cls.cma.REGISTRAR_MOVIMIENTO_PATH.startswith(tmp_centro_dir), \
            f"REGISTRAR_MOVIMIENTO_PATH no apunta al temp: {cls.cma.REGISTRAR_MOVIMIENTO_PATH}"

        with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
            cls.casos = json.load(f)["casos"]

        cls._extraction_by_raw_text = {}
        for caso in cls.casos:
            raw_text = RAW_TEXT_BY_CONCEPT[caso["concept"]]
            cls._extraction_by_raw_text[raw_text] = {
                "ok": True,
                "provider": "stub-para-test",
                "data": {
                    "domain": "cfo",
                    "intent": TIPO_TO_INTENT[caso["tipo"]],
                    "amount": caso["amount"],
                    "date": caso["date"],
                    "concept": caso["concept"],
                    "evidence": raw_text,
                },
            }

        def _stub_extract(raw_text):
            return cls._extraction_by_raw_text[raw_text]

        cls._real_extract = cls.ai_router.extract
        cls.ai_router.extract = _stub_extract
        cls.worker.ai_router.extract = _stub_extract

    @classmethod
    def tearDownClass(cls):
        cls.ai_router.extract = cls._real_extract
        shutil.rmtree(cls.tmp_root, ignore_errors=True)

    def test_10_casos_flujo_completo_hasta_completed(self):
        completados = []
        for i, caso in enumerate(self.casos):
            raw_text = RAW_TEXT_BY_CONCEPT[caso["concept"]]
            with self.subTest(concept=caso["concept"]):
                result = self.tasks.create_task(f"dry-run-1707-{i}", "nicolas", None, raw_text)
                task_id = result["task"]["task_id"]

                task = self.tasks.get_task_dict(task_id)
                self.worker._process_classification(task)

                task = self.tasks.get_task_dict(task_id)
                self.assertEqual(task["state"], "ready", f"'{caso['concept']}' no llegó a 'ready' (quedó en {task['state']})")

                self.worker._process_execution(task)

                task = self.tasks.get_task_dict(task_id)
                self.assertEqual(task["state"], "completed", f"'{caso['concept']}' no llegó a 'completed' (quedó en {task['state']})")
                completados.append(task_id)

        self.assertEqual(len(completados), 10)

    def test_archivos_temporales_reflejan_los_10_movimientos(self):
        # Corre después del test anterior (unittest ordena alfabéticamente:
        # "10_casos" antes de "archivos") -- confirma el efecto en disco, no
        # solo el estado en la base.
        with open(self.tmp_foto, "r", encoding="utf-8") as f:
            contenido_foto = f.read()
        for caso in self.casos:
            self.assertIn(caso["concept"], contenido_foto, f"'{caso['concept']}' no apareció en la foto financiera temporal")

        self.assertTrue(os.path.exists(self.tmp_registro_entrada))
        with open(self.tmp_registro_entrada, "r", encoding="utf-8") as f:
            registro = f.read()
        self.assertEqual(registro.count("agregado a"), 10)

        with open(self.tmp_ledger, "r", encoding="utf-8") as f:
            operation_ids = [line.strip() for line in f if line.strip()]
        self.assertEqual(len(operation_ids), 10)
        self.assertEqual(len(set(operation_ids)), 10, "operation_ids no deberían repetirse")

    def test_no_toca_archivos_reales(self):
        post_snapshot = _hash_dir_snapshot()
        self.assertEqual(
            self.pre_snapshot, post_snapshot,
            "¡Este test modificó un archivo real de Centro de Mando o CFO y Decisiones Estratégicas! "
            "Eso es exactamente lo que Etapa 10 tiene que garantizar que nunca pase.",
        )


if __name__ == "__main__":
    unittest.main()
