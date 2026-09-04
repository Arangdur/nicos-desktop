"""
v0.2.1-rc6, requisito 2: domain="unknown" (o cualquier valor fuera de
{cfo,abate}) no debe provocar NINGÚN llamado automático adicional -- debe
pasar directo a 'needs_information', con un error_message que explique qué
falta. Este es el caso central del bug hallado en el smoke test del
18/7/2026 (la tarea quedaba atascada en 'parsing' reintentando para siempre).

Uso: python3 sidecar/tests/test_extraction_domain_unknown.py
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


class TestExtractionDomainUnknown(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_domain_unknown_va_a_needs_information_sin_llamado_extra(self):
        # v0.2.13 -- extract_task -> "claude" por default en
        # provider_matrix.json, así que el primario real acá es Claude --
        # OpenAI ni se toca.
        claude_client, _ = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown", intent="other", amount=None,
                                                     date=None, concept=None, evidence="mención de trading, fuera de alcance")],
        )
        result = self.tasks.create_task("domain-unknown-1", "marianela", None, "Cambié la posición del bot en BTC")
        task = result["task"]

        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "needs_information")
        self.assertIsNotNone(final["error_message"])
        self.assertIn("ambiguo", final["error_message"].lower())
        # NO se le dio la oportunidad al alternativo -- domain=unknown es una
        # extracción EXITOSA y estructuralmente válida, no un fallo de proveedor.
        self.assertEqual(claude_client.call_count, 1)

    def test_ui_puede_reconstruir_que_informacion_falta(self):
        """No hace falta abrir Electron para esta parte: alcanza con confirmar
        que el dato que la UI (tasks-tab.js) va a mostrar -- error_message y
        extracted_json -- efectivamente queda poblado y es legible."""
        fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown")],
        )
        result = self.tasks.create_task("domain-unknown-2", "marianela", None, "algo ambiguo")
        task = result["task"]
        self.worker._process_classification(task)
        final = self.tasks.get_task_dict(task["task_id"])
        self.assertTrue(final["error_message"])
        self.assertIsInstance(final["extracted_json"], dict)
        self.assertEqual(final["extracted_json"]["domain"], "unknown")

    def test_amount_como_string_tambien_es_ambiguo_no_llega_a_ready(self):
        """No es domain=unknown, pero es el mismo espíritu del requisito 2:
        datos insuficientes/incorrectos no deben colarse a 'ready'. Antes del
        fix esto llegaba a 'ready' sin validar el tipo de amount."""
        fake.install_fake_clients(
            self.ai_router,
            claude_behavior=["malformed"],  # amount no numérico -> forma inválida
        )
        # Nota: acá se prueba la validación de forma en ai_router directamente
        # (ver test_extraction_malformed_json.py para el flujo completo).
        outcome = self.ai_router._try_extract("claude", "gasto de $x")
        self.assertEqual(outcome["status"], "malformed")


if __name__ == "__main__":
    unittest.main()
