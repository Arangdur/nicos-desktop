"""Prueba de solo lectura: último comprobante autorizado para Pto Vta 4,
Factura C (tipo 11, la que corresponde a Monotributo). Uso: python3 test_ultimo_cbte.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import wsaa_client
import wsfe_client

CERT = os.path.expanduser("~/Desktop/arca-certificado/nicosbot.crt")
KEY = os.path.expanduser("~/Desktop/arca-certificado/privada.key")
CUIT = "20285807213"
PTO_VENTA = 4
TIPO_CBTE = 11  # Factura C

if __name__ == "__main__":
    creds = wsaa_client.obtener_credenciales(CERT, KEY)
    try:
        ultimo = wsfe_client.consultar_ultimo_autorizado(CUIT, creds["token"], creds["sign"], PTO_VENTA, TIPO_CBTE)
        print(f"Último comprobante autorizado (Pto Vta {PTO_VENTA}, Factura C): {ultimo}")
        print(f"El próximo sería el número: {ultimo + 1}")
    except wsfe_client.WsfeError as e:
        print("ERROR:", e)
        sys.exit(1)
