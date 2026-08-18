"""Prueba manual: solo pide credenciales a WSAA, no toca WSFE ni factura nada.
Uso: python3 test_wsaa.py"""
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import wsaa_client

CERT = os.path.expanduser("~/Desktop/arca-certificado/nicosbot.crt")
KEY = os.path.expanduser("~/Desktop/arca-certificado/privada.key")

if __name__ == "__main__":
    try:
        creds = wsaa_client.obtener_credenciales(CERT, KEY)
        print("OK -- WSAA devolvió credenciales válidas.")
        print("token (primeros 40 chars):", creds["token"][:40], "...")
        print("sign (primeros 40 chars):", creds["sign"][:40], "...")
    except wsaa_client.WsaaError as e:
        print("ERROR:", e)
        sys.exit(1)
