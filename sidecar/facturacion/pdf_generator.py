"""
Genera el PDF de una factura ya emitida (con CAE real) -- incluye el QR
oficial que exige ARCA (RG 4892) para que cualquiera pueda verificarla en
https://www.afip.gob.ar/fe/qr/.

ponytail: `segno` para el QR (puro Python, sin arrastrar Pillow) + `fpdf2`
para el PDF (puro Python también) -- las dos dependencias más chicas que
resuelven esto, nada de un motor de templates ni un renderer HTML->PDF para
un documento de una sola página con layout fijo.
"""
import base64
import io
import json

import segno
from fpdf import FPDF

EMISOR = {
    "razon_social": "Nicolás Buso",
    "cuit": "20-28580721-3",
    "domicilio": "9 de Julio 1157, Justiniano Posse, Córdoba",
    "condicion_iva": "Monotributista",
    "actividad": "Servicios relacionados con la salud humana n.c.p.",
}

TIPO_CBTE_LETRA = {1: "A", 6: "B", 11: "C", 13: "C"}
TIPO_CBTE_NOMBRE = {1: "FACTURA A", 6: "FACTURA B", 11: "FACTURA C", 13: "NOTA DE CRÉDITO C"}
CONDICION_IVA_RECEPTOR = {80: "CUIT", 86: "CUIL", 96: "DNI", 99: "Consumidor Final"}


def _armar_qr_payload(datos: dict) -> str:
    """`datos` viene con las mismas keys que devuelve wsfe_client.solicitar_cae
    más lo que hizo falta para pedir el CAE (importe, doc_tipo, doc_nro,
    fecha). Arma el JSON que exige ARCA y lo empaqueta en la URL del QR."""
    payload = {
        "ver": 1,
        "fecha": datos["fecha_iso"],
        "cuit": int(EMISOR["cuit"].replace("-", "")),
        "ptoVta": datos["pto_venta"],
        "tipoCmp": datos["tipo_cbte"],
        "nroCmp": datos["numero"],
        "importe": round(datos["importe"], 2),
        "moneda": "PES",
        "ctz": 1,
        "tipoDocRec": datos["doc_tipo"],
        "nroDocRec": datos["doc_nro"],
        "tipoCodAut": "E",
        "codAut": int(datos["cae"]),
    }
    b64 = base64.b64encode(json.dumps(payload).encode("utf-8")).decode("ascii")
    return f"https://www.afip.gob.ar/fe/qr/?p={b64}"


def _qr_png_bytes(url: str) -> bytes:
    qr = segno.make(url, error="m")
    buf = io.BytesIO()
    qr.save(buf, kind="png", scale=4, border=2)
    return buf.getvalue()


def generar_pdf(datos: dict, ruta_salida: str) -> str:
    """`datos` = dict con: numero, pto_venta, tipo_cbte, cae, vencimiento
    (CAEFchVto formato YYYYMMDD), importe, concepto_texto, fecha_iso
    (YYYY-MM-DD), doc_tipo, doc_nro, cliente_nombre (opcional, default
    'Consumidor Final'). Escribe el PDF en `ruta_salida` y devuelve esa
    misma ruta."""
    letra = TIPO_CBTE_LETRA.get(datos["tipo_cbte"], "?")
    nombre_cbte = TIPO_CBTE_NOMBRE.get(datos["tipo_cbte"], "COMPROBANTE")
    fecha_fmt = datos["fecha_iso"][8:10] + "/" + datos["fecha_iso"][5:7] + "/" + datos["fecha_iso"][0:4]
    vto_cae = datos["vencimiento"]
    vto_cae_fmt = f"{vto_cae[6:8]}/{vto_cae[4:6]}/{vto_cae[0:4]}" if vto_cae else "-"
    cliente_nombre = datos.get("cliente_nombre") or "Consumidor Final"
    # doc_tipo 99 = Consumidor Final -- ahí no hay CUIT/DNI real que mostrar,
    # una segunda línea repitiendo "Consumidor Final" es ruido, no dato.
    cliente_doc_linea = (
        f"{CONDICION_IVA_RECEPTOR.get(datos['doc_tipo'], 'Doc.')}: {datos['doc_nro']}"
        if datos["doc_tipo"] != 99 else ""
    )

    pdf = FPDF(format="A4", unit="mm")
    pdf.add_page()
    pdf.set_auto_page_break(auto=False)

    # -- Recuadro con la letra del comprobante, arriba al centro --
    pdf.set_font("Helvetica", "B", 22)
    pdf.set_xy(95, 10)
    pdf.cell(20, 14, letra, border=1, align="C")

    # -- Emisor (izquierda) --
    pdf.set_xy(10, 12)
    pdf.set_font("Helvetica", "B", 13)
    pdf.cell(80, 6, EMISOR["razon_social"])
    pdf.set_xy(10, 19)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(80, 4.5, f"{EMISOR['domicilio']}\n{EMISOR['condicion_iva']}\nActividad: {EMISOR['actividad']}")

    # -- Datos del comprobante (derecha) --
    pdf.set_xy(130, 12)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(70, 6, nombre_cbte)
    pdf.set_xy(130, 19)
    pdf.set_font("Helvetica", "", 9)
    pdf.multi_cell(
        70, 4.5,
        f"Punto de Venta: {datos['pto_venta']:04d}   Comp. Nro: {datos['numero']:08d}\n"
        f"Fecha de Emisión: {fecha_fmt}\n"
        f"CUIT: {EMISOR['cuit']}",
    )

    pdf.set_draw_color(0, 0, 0)
    pdf.line(10, 40, 200, 40)

    # -- Receptor --
    pdf.set_xy(10, 44)
    pdf.set_font("Helvetica", "B", 10)
    pdf.cell(0, 5, "Cliente")
    pdf.set_xy(10, 50)
    pdf.set_font("Helvetica", "", 9)
    texto_cliente = f"{cliente_nombre}\n{cliente_doc_linea}" if cliente_doc_linea else cliente_nombre
    pdf.multi_cell(0, 4.5, texto_cliente)

    pdf.line(10, 62, 200, 62)

    # -- Detalle --
    pdf.set_xy(10, 66)
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(150, 6, "Descripción", border="B")
    pdf.cell(40, 6, "Importe", border="B", align="R")
    pdf.ln(6)
    pdf.set_font("Helvetica", "", 9)
    pdf.set_x(10)
    pdf.cell(150, 6, datos["concepto_texto"])
    pdf.cell(40, 6, f"$ {datos['importe']:,.2f}", align="R")

    pdf.line(10, 78, 200, 78)

    pdf.set_xy(120, 82)
    pdf.set_font("Helvetica", "B", 12)
    pdf.cell(70, 8, f"Importe Total: $ {datos['importe']:,.2f}", align="R")

    # -- CAE + QR (pie de página) --
    qr_bytes = _qr_png_bytes(_armar_qr_payload(datos))
    qr_path = ruta_salida + ".qr.png"
    with open(qr_path, "wb") as f:
        f.write(qr_bytes)
    pdf.image(qr_path, x=10, y=100, w=30)
    import os
    os.unlink(qr_path)

    pdf.set_xy(45, 105)
    pdf.set_font("Helvetica", "", 9)
    texto_cae = f"CAE N°: {datos['cae']}\nFecha de Vto. de CAE: {vto_cae_fmt}"
    # Solo presente en una Nota de Crédito -- ARCA exige mostrar a qué
    # comprobante corrige.
    if datos.get("comprobante_asociado_texto"):
        texto_cae += f"\nComprobante asociado: {datos['comprobante_asociado_texto']}"
    pdf.multi_cell(0, 5, texto_cae)

    pdf.output(ruta_salida)
    return ruta_salida
