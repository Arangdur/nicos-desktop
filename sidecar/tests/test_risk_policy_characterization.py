"""
Prueba de caracterización: classify_request()/evaluate_risk() deben producir
EXACTAMENTE el mismo resultado que produjeron para los 10 movimientos reales
del 17/07/2026 (ver fixtures/casos_17_07.json), sin importar si la política
vive en constantes de Python o en policies/risk_policy.yaml.

No toca ningún archivo real ni ejecuta registrar_movimiento.py -- solo llama
a las dos funciones deterministas de clasificación/riesgo. Correr esto antes
Y después de tocar risk_policy.yaml; si deja de pasar, algo cambió la regla
sin querer.

Uso: python3 sidecar/tests/test_risk_policy_characterization.py
"""
import json
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import centro_mando_adapter as cma

FIXTURES_PATH = os.path.join(os.path.dirname(__file__), "fixtures", "casos_17_07.json")

TIPO_TO_INTENT = {"gasto": "register_expense", "ingreso": "register_income"}


class TestRiskPolicyCharacterization(unittest.TestCase):
    def setUp(self):
        with open(FIXTURES_PATH, "r", encoding="utf-8") as f:
            self.casos = json.load(f)["casos"]

    def test_10_casos_reales_17_07(self):
        self.assertEqual(len(self.casos), 10, "deberían ser los 10 movimientos reales del 17/07/2026")
        for caso in self.casos:
            extracted = {"domain": "cfo", "concept": caso["concept"], "amount": caso["amount"], "date": caso["date"]}
            intent = TIPO_TO_INTENT[caso["tipo"]]

            domain = cma.classify_request(extracted)
            risk = cma.evaluate_risk(domain, intent, extracted)

            with self.subTest(concept=caso["concept"]):
                self.assertEqual(domain, caso["expected_domain"], f"dominio cambió para '{caso['concept']}'")
                self.assertEqual(intent, caso["expected_intent"], f"intent cambió para '{caso['concept']}'")
                self.assertEqual(risk, caso["expected_risk"], f"nivel de riesgo cambió para '{caso['concept']}'")

    def test_dominio_no_soportado_sigue_bloqueado(self):
        # Guarda de regresión: un dominio clínico (o cualquier otro no listado
        # en supported_domains) nunca debe clasificar como válido, migración
        # de YAML incluida.
        self.assertIsNone(cma.classify_request({"domain": "clinico"}))
        self.assertIsNone(cma.classify_request({"domain": None}))
        self.assertIsNone(cma.classify_request({}))

    def test_intent_no_logging_requiere_aprobacion(self):
        # Cualquier intent fuera de logging_intents (ej. "transfer_funds",
        # inventado) tiene que quedar en approval_required por default seguro.
        extracted = {"domain": "cfo", "concept": "x", "amount": 1, "date": "2026-07-17"}
        self.assertEqual(cma.evaluate_risk("cfo", "transfer_funds", extracted), "approval_required")

    def test_campo_faltante_requiere_aprobacion(self):
        extracted_incompleto = {"domain": "cfo", "concept": "x", "amount": 1}  # sin date
        self.assertEqual(cma.evaluate_risk("cfo", "register_expense", extracted_incompleto), "approval_required")


if __name__ == "__main__":
    unittest.main()
