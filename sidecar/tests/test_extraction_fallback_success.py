"""
v0.2.1-rc6: reproduce el caso REAL observado en el smoke test del 18/7/2026
(cuenta de OpenAI sin crédito, 429 insufficient_quota) -- el proveedor
primario falla por cuota, se intenta UNA vez con el alternativo, y la
extracción se completa igual, quedando registrado que hubo un fallback (para
que no se confunda con una extracción "limpia" del primario).

Uso: python3 sidecar/tests/test_extraction_fallback_success.py
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


class TestExtractionFallbackSuccess(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    # v0.2.13 -- extract_task pasó a tener a Claude como primario (ver
    # provider_matrix.json) -- todo este archivo prueba "el primario falla,
    # el alternativo completa"; ahora el primario que falla es Claude y el
    # alternativo que completa es OpenAI (roles dados vuelta, mismo caso).

    def test_429_en_primario_cae_al_alternativo_y_completa_la_extraccion(self):
        claude_client, openai_client = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.RateLimitError(
                "Error code: 429 - insufficient_quota: You exceeded your current quota"
            )],
            openai_behavior=[fake.valid_extraction(
                domain="cfo", intent="register_income", amount=23456,
                date="18/07/2026", concept="Devolución de una compra",
            )],
        )
        result = self.tasks.create_task("fallback-1", "marianela", None,
                                         "Registrar un ingreso de $23.456 por devolución")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        self.assertEqual(final["state"], "ready")
        self.assertEqual(final["domain"], "cfo")
        self.assertEqual(final["extracted_json"]["amount"], 23456)
        self.assertEqual(claude_client.call_count, 1)
        self.assertEqual(openai_client.call_count, 1)

    def test_fallback_queda_registrado_en_el_evento_de_clasificado(self):
        """El Director tiene que poder ver, mirando el historial, que la
        extracción NO vino del proveedor primario -- no alcanza con que
        'funcionó', tiene que quedar trazado."""
        fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.RateLimitError("429")],
            openai_behavior=[fake.valid_extraction()],
        )
        result = self.tasks.create_task("fallback-2", "marianela", None, "algo")
        task = result["task"]
        self.worker._process_classification(task)

        events = self.tasks.get_task_events(task["task_id"])
        classified_event = next(e for e in events if e["to_state"] == "classified")
        detail = classified_event["detail_json"]
        if isinstance(detail, str):
            import json
            detail = json.loads(detail)
        self.assertEqual(detail.get("fell_back_from"), "claude")
        self.assertEqual(detail.get("extraction_provider"), "openai")

    def test_fallback_no_aplica_solo_a_tareas_verdes(self):
        """Hallazgo del smoke test: el nombre viejo (fallback_allowed_for_
        simple_tasks) prometía algo que el código nunca cumplió, porque el
        riesgo recién se conoce DESPUÉS de la extracción. Se prueba con un
        intent que NO es de logging (new_financial_action -> approval_
        required) para confirmar que el fallback igual se aplica -- es
        correcto y esperado con el nombre nuevo (fallback_on_primary_failure),
        que ya no promete lo contrario. Dominio 'abate' porque prepare_action
        todavía no soporta new_financial_action para 'cfo' (limitación
        preexistente, no relacionada con este fix -- ver UnsupportedDomain en
        centro_mando_adapter.prepare_action)."""
        fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.RateLimitError("429")],
            openai_behavior=[fake.valid_extraction(domain="abate", intent="new_financial_action", amount=99999,
                                                     concept="proveedor nuevo")],
        )
        result = self.tasks.create_task("fallback-3", "marianela", None,
                                         "Autorizar un pago nuevo de $99.999 a un proveedor de Abate")
        task = result["task"]
        self.worker._process_classification(task)

        final = self.tasks.get_task_dict(task["task_id"])
        # new_financial_action nunca es "simple" -- pending_approval, no ready.
        self.assertEqual(final["state"], "pending_approval")
        self.assertEqual(final["risk_level"], "approval_required")
        # y sin embargo el fallback SÍ se usó -- confirma que no está
        # condicionado a que la tarea termine siendo "verde".
        events = self.tasks.get_task_events(task["task_id"])
        classified_event = next(e for e in events if e["to_state"] == "classified")
        detail = classified_event["detail_json"]
        if isinstance(detail, str):
            import json
            detail = json.loads(detail)
        self.assertEqual(detail.get("fell_back_from"), "claude")


if __name__ == "__main__":
    unittest.main()
