"""
Prueba de la defensa adicional de datos clínicos (server.py, POST /api/v1/tasks).
No es la regla real (esa es que el extractor de dominio nunca devuelve
"clinico") -- esto solo confirma que la heurística de defensa en profundidad
sigue bloqueando lo esperado y sigue dejando pasar texto normal de cfo/abate.

Uso: python3 sidecar/tests/test_clinical_guard.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import clinical_guard as cg


class TestClinicalGuard(unittest.TestCase):
    def test_texto_financiero_normal_no_se_bloquea(self):
        casos = [
            "Registrar pago de electricidad de Abate por $450.000",
            "Cargá un ingreso de $1.200.000 de Hospital Posse",
            "Recordar comprar resina epoxi",
            "El total de gastos de julio fue 450000",
        ]
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertFalse(cg.detect_clinical_data(texto)["blocked"])

    def test_dni_con_contexto_se_bloquea(self):
        self.assertTrue(cg.detect_clinical_data("DNI 32.456.789 pidió turno para el jueves")["blocked"])

    def test_numero_de_7_8_digitos_sin_dni_no_se_bloquea(self):
        # Un monto grande (ej. $1.755.000) no debe confundirse con un DNI --
        # la heurística exige la palabra "dni" presente, no solo el patrón numérico.
        self.assertFalse(cg.detect_clinical_data("Ingreso de $1.755.000 de consultas")["blocked"])

    def test_palabras_clave_clinicas_se_bloquean(self):
        casos = [
            "El paciente Juan Pérez tiene diagnóstico de trastorno de ansiedad",
            "Le hice la receta de sertralina al paciente",
            "Actualizar la historia clínica de hoy",
        ]
        for texto in casos:
            with self.subTest(texto=texto):
                self.assertTrue(cg.detect_clinical_data(texto)["blocked"])

    def test_nombre_apellido_mas_paciente_se_bloquea(self):
        self.assertTrue(cg.detect_clinical_data("Turno con paciente Maria Gonzalez a las 10")["blocked"])

    def test_texto_vacio_no_se_bloquea(self):
        self.assertFalse(cg.detect_clinical_data("")["blocked"])
        self.assertFalse(cg.detect_clinical_data(None)["blocked"])


if __name__ == "__main__":
    unittest.main()
