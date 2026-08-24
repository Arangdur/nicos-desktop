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
        worker._worker_iniciado_en = "2026-08-24T10:00:00"
        worker._ultimo_tick_ok = "2026-08-24T10:05:00"
        worker._errores_atajados = [{"at": "2026-08-24T10:03:00", "error": "boom"}]
        estado = worker.estado_worker()
        self.assertEqual(estado["iniciado_en"], "2026-08-24T10:00:00")
        self.assertEqual(estado["ultimo_tick_ok"], "2026-08-24T10:05:00")
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
