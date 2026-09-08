"""
v0.2.15 (08/09) -- dominio nuevo "consultorio": pedidos operativos del
consultorio particular ("necesito bono pap de tal paciente"), sin plata de
por medio -- nunca se automatiza, mismo tratamiento que "abate" hoy (ver
centro_mando_adapter.prepare_action/execute_action).

Uso: python3 sidecar/tests/test_domain_consultorio.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import centro_mando_adapter as cma  # noqa: E402


class TestDomainConsultorio(unittest.TestCase):
    def test_consultorio_esta_en_los_dominios_soportados(self):
        self.assertIn("consultorio", cma.SUPPORTED_DOMAINS)

    def test_classify_request_acepta_consultorio(self):
        self.assertEqual(cma.classify_request({"domain": "consultorio"}), "consultorio")

    def test_prepare_action_arma_una_nota_no_una_ejecucion_real(self):
        prepared = cma.prepare_action("consultorio", "other", {
            "concept": "bono pap", "evidence": "paciente Fulano, cantidad 2",
        })
        self.assertEqual(prepared["domain"], "consultorio")
        self.assertIn("nota", prepared)
        self.assertEqual(prepared["args"]["pedido"], "bono pap")

    def test_execute_action_nunca_ejecuta_solo_pide_revision_manual(self):
        prepared = cma.prepare_action("consultorio", "other", {"concept": "bono pap"})
        result = cma.execute_action("task-id-no-usado", prepared)
        self.assertFalse(result["ok"])
        self.assertTrue(result["needs_manual_review"])

    def test_prepare_action_ignora_el_intent_para_consultorio(self):
        # a diferencia de "cfo" (donde el intent decide gasto/ingreso),
        # "consultorio" nunca se automatiza -- cualquier intent arma la misma
        # nota de "respondé a mano", no rompe con UnsupportedDomain.
        for intent in ("register_expense", "register_income", "new_financial_action", "other"):
            prepared = cma.prepare_action("consultorio", intent, {"concept": "algo"})
            self.assertEqual(prepared["command"], "responder_a_mano")


if __name__ == "__main__":
    unittest.main()
