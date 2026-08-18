"""Genera el PDF de la factura de prueba ya emitida (CAE real 86327921358301).
No llama a ARCA, solo arma el PDF con los datos que ya tenemos. Uso: python3 test_pdf.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pdf_generator

datos = {
    "numero": 1,
    "pto_venta": 4,
    "tipo_cbte": 11,
    "cae": "86327921358301",
    "vencimiento": "20260821",
    "importe": 1000.00,
    "concepto_texto": "Servicios profesionales (prueba de sistema)",
    "fecha_iso": "2026-08-11",
    "doc_tipo": 99,
    "doc_nro": 0,
}

ruta = os.path.expanduser("~/Desktop/factura_prueba.pdf")
resultado = pdf_generator.generar_pdf(datos, ruta)
print("PDF generado:", resultado)
