"""EMITE UNA FACTURA REAL EN ARCA. Irreversible. Solo correr con datos
confirmados por Nicolás. Uso: python3 emitir_prueba.py"""
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
CONCEPTO_TEXTO = "Servicios profesionales (prueba de sistema)"
IMPORTE = 1000.00

if __name__ == "__main__":
    creds = wsaa_client.obtener_credenciales(CERT, KEY)
    ultimo = wsfe_client.consultar_ultimo_autorizado(CUIT, creds["token"], creds["sign"], PTO_VENTA, TIPO_CBTE)
    proximo = ultimo + 1

    print(f"Emitiendo Factura C N°{proximo}, Pto Vta {PTO_VENTA}, ${IMPORTE:.2f}, Consumidor Final...")
    try:
        resultado = wsfe_client.solicitar_cae(
            CUIT, creds["token"], creds["sign"], PTO_VENTA, TIPO_CBTE, proximo, IMPORTE,
        )
    except wsfe_client.WsfeError as e:
        print("ERROR -- no se emitió nada:", e)
        sys.exit(1)

    print("OK -- Factura emitida:")
    print("  Número:", resultado["numero"])
    print("  CAE:", resultado["cae"])
    print("  Vencimiento CAE:", resultado["vencimiento"])
