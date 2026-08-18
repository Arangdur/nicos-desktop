"""EMITE UNA FACTURA REAL EN ARCA. Irreversible. Datos confirmados por
Nicolás (13/08/2026): Mariana Gauna, CUIL 27314041912, $70.000, honorarios
por consulta médica psiquiatría.
Uso: python3 emitir_mariana_gauna.py"""
import datetime
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import pdf_generator
import wsaa_client
import wsfe_client

CERT = os.path.expanduser("~/Desktop/arca-certificado/nicosbot.crt")
KEY = os.path.expanduser("~/Desktop/arca-certificado/privada.key")
CUIT = "20285807213"
PTO_VENTA = 4
TIPO_CBTE = 11  # Factura C

CLIENTE_NOMBRE = "Mariana Gauna"
DOC_TIPO = 86  # CUIL
DOC_NRO = 27314041912
CONCEPTO_TEXTO = "Honorarios por consulta médica psiquiatría"
IMPORTE = 70000.00

if __name__ == "__main__":
    creds = wsaa_client.obtener_credenciales(CERT, KEY)
    ultimo = wsfe_client.consultar_ultimo_autorizado(CUIT, creds["token"], creds["sign"], PTO_VENTA, TIPO_CBTE)
    proximo = ultimo + 1

    print(f"Emitiendo Factura C N°{proximo}, Pto Vta {PTO_VENTA}, ${IMPORTE:.2f}, {CLIENTE_NOMBRE} (CUIL {DOC_NRO})...")
    try:
        resultado = wsfe_client.solicitar_cae(
            CUIT, creds["token"], creds["sign"], PTO_VENTA, TIPO_CBTE, proximo, IMPORTE,
            doc_tipo=DOC_TIPO, doc_nro=DOC_NRO,
        )
    except wsfe_client.WsfeError as e:
        print("ERROR -- no se emitió nada:", e)
        sys.exit(1)

    print("OK -- Factura emitida:")
    print("  Número:", resultado["numero"])
    print("  CAE:", resultado["cae"])
    print("  Vencimiento CAE:", resultado["vencimiento"])

    pdf_dir = os.path.join(os.path.dirname(__file__), "..", "facturas_pdf")
    os.makedirs(pdf_dir, exist_ok=True)
    pdf_path = os.path.join(pdf_dir, f"factura_mariana_gauna_{proximo}.pdf")
    pdf_generator.generar_pdf({
        "numero": proximo, "pto_venta": PTO_VENTA, "tipo_cbte": TIPO_CBTE,
        "cae": resultado["cae"], "vencimiento": resultado["vencimiento"],
        "importe": IMPORTE, "concepto_texto": CONCEPTO_TEXTO,
        "fecha_iso": datetime.date.today().isoformat(),
        "doc_tipo": DOC_TIPO, "doc_nro": DOC_NRO, "cliente_nombre": CLIENTE_NOMBRE,
    }, pdf_path)
    print("  PDF:", pdf_path)
