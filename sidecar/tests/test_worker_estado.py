"""
v0.2.8 -- "pulso del worker" en el Resumen del Director. Chequeo mínimo de
estado_worker(): arranca en None/0, y refleja errores atajados recortados a
ERRORES_ATAJADOS_MAX.

Uso: python3 sidecar/tests/test_worker_estado.py
"""
import datetime
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import worker  # noqa: E402


class TestEstadoWorker(unittest.TestCase):
    def setUp(self):
        # aislar del estado real que pudiera haber dejado otro test del módulo
        worker._worker_iniciado_en = None
        worker._ultimo_tick_ok = None
        worker._errores_atajados = []

    def test_sin_arrancar_nunca_todo_vacio(self):
        estado = worker.estado_worker()
        self.assertIsNone(estado["iniciado_en"])
        self.assertIsNone(estado["ultimo_tick_ok"])
        self.assertEqual(estado["errores_atajados_24hs"], 0)
        self.assertIsNone(estado["ultimo_error"])

    def test_refleja_ultimo_tick_y_ultimo_error(self):
        # v0.2.9 -- eran fechas fijas ("2026-08-24...") que dejaban de estar
        # dentro de la ventana de 24hs apenas pasaba la medianoche real y
        # rompían el test al día siguiente. Ahora relativas a "ahora".
        ahora = datetime.datetime.utcnow()
        worker._worker_iniciado_en = (ahora - datetime.timedelta(minutes=10)).isoformat()
        worker._ultimo_tick_ok = (ahora - datetime.timedelta(minutes=5)).isoformat()
        worker._errores_atajados = [{"at": (ahora - datetime.timedelta(minutes=7)).isoformat(), "error": "boom"}]
        estado = worker.estado_worker()
        self.assertEqual(estado["iniciado_en"], worker._worker_iniciado_en)
        self.assertEqual(estado["ultimo_tick_ok"], worker._ultimo_tick_ok)
        self.assertEqual(estado["errores_atajados_24hs"], 1)
        self.assertEqual(estado["ultimo_error"]["error"], "boom")

    def test_errores_viejos_de_mas_de_24hs_no_cuentan(self):
        viejo = (datetime.datetime.utcnow() - datetime.timedelta(hours=48)).isoformat()
        worker._errores_atajados = [{"at": viejo, "error": "viejo"}]
        estado = worker.estado_worker()
        self.assertEqual(estado["errores_atajados_24hs"], 0)
        # ultimo_error sigue mostrando el más reciente aunque sea viejo -- es
        # informativo, no está filtrado por ventana de 24hs
        self.assertEqual(estado["ultimo_error"]["error"], "viejo")


if __name__ == "__main__":
    unittest.main()
