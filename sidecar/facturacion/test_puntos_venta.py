"""Prueba de solo lectura: consulta puntos de venta habilitados. No emite
nada, no tiene efecto fiscal. Uso: python3 test_puntos_venta.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import wsaa_client
import wsfe_client

CERT = os.path.expanduser("~/Desktop/arca-certificado/nicosbot.crt")
KEY = os.path.expanduser("~/Desktop/arca-certificado/privada.key")
CUIT = "20285807213"

if __name__ == "__main__":
    creds = wsaa_client.obtener_credenciales(CERT, KEY)
    try:
        puntos = wsfe_client.consultar_puntos_venta(CUIT, creds["token"], creds["sign"])
        if not puntos:
            print("No hay puntos de venta habilitados para web service todavía.")
        for p in puntos:
            print(p)
    except wsfe_client.WsfeError as e:
        print("ERROR:", e)
        sys.exit(1)
