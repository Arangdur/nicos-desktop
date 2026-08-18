"""
Bandeja de pedidos de factura por WhatsApp -- bot de facturación ARCA (ver
CFO y Decisiones Estrategicas/Proyecto_Facturacion_WhatsApp_ARCA.md). Mismo
patrón que mensajes_whatsapp.py:

  alguien escribe "facturale X a Y" -> 'recibido' -> worker le pide a la IA
  que extraiga monto/concepto/cliente -> 'borrador_generado' -> Nicolás
  aprueba -> ahí recién se pide el CAE REAL a ARCA, se genera el PDF y se
  manda por WhatsApp.

Diferencia deliberada con mensajes_whatsapp.py: acá la aprobación es SIEMPRE
Director-only, sin excepción -- emitir una factura real en ARCA es un acto
fiscal, no hay equivalente al "no clínico, lo puede aprobar Operativa" de
los mensajes a pacientes.

La IA NUNCA emite sola -- solo arma el borrador. Regla de oro no negociable,
igual que en mensajes_whatsapp.py.
"""
import datetime
import os
import secrets

import ai_router
import db
import twilio_client
from facturacion import pdf_generator, wsaa_client, wsfe_client

ESTADOS_VALIDOS = {"recibido", "borrador_generado", "error_extraccion", "emitida", "rechazada", "error_emision"}

# Fijo por ahora -- un solo Punto de Venta habilitado para Web Services
# (N°4, dado de alta en ARCA el 11/08/2026), Factura C porque Nicolás
# factura como Monotributo (no discrimina IVA). Si en el futuro factura
# desde otra condición fiscal, esto pasa a ser configurable.
PTO_VENTA = 4
TIPO_CBTE_FACTURA = 11
TIPO_CBTE_NOTA_CREDITO = 13
CUIT_EMISOR = "20285807213"

CERT_PATH = os.getenv("ARCA_CERT_PATH", os.path.expanduser("~/Desktop/arca-certificado/nicosbot.crt"))
KEY_PATH = os.getenv("ARCA_KEY_PATH", os.path.expanduser("~/Desktop/arca-certificado/privada.key"))
PDF_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "facturas_pdf")


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


class FacturaError(Exception):
    pass


class RequiereDirector(FacturaError):
    """Aprobar/rechazar una factura es SIEMPRE Director-only -- server.py
    atrapa esto y responde 403."""
    pass


def registrar_pedido_factura(telefono: str, texto: str) -> dict:
    """Llamado por el handler del webhook apenas llega un mensaje que
    empieza a parecer un pedido de factura -- ANTES de cualquier extracción,
    mismo principio que mensajes_whatsapp.registrar_mensaje_entrante: nunca
    perder el pedido original por un error de la IA."""
    if not telefono or not texto:
        raise FacturaError("Falta teléfono o texto del pedido.")
    conn = db.get_connection()
    factura_id = secrets.token_hex(8)
    now = _now_iso()
    conn.execute(
        "INSERT INTO facturas_whatsapp (id, telefono, texto_original, estado, recibido_at) "
        "VALUES (?, ?, ?, 'recibido', ?)",
        (factura_id, telefono, texto, now),
    )
    conn.commit()
    return {"id": factura_id}


def pedidos_pendientes_de_borrador() -> list:
    conn = db.get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM facturas_whatsapp WHERE estado = 'recibido' ORDER BY recibido_at"
    ).fetchall()]


def generar_borrador(factura_id: str) -> dict:
    """Un único intento de extracción para este pedido -- mismo criterio que
    mensajes_whatsapp.generar_borrador: si falla, queda en 'error_extraccion'
    con el texto original intacto, nunca se pierde el pedido."""
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM facturas_whatsapp WHERE id = ?", (factura_id,)).fetchone()
    if row is None:
        raise FacturaError(f"Pedido no encontrado: {factura_id}")

    resultado = ai_router.extraer_pedido_factura(row["texto_original"])
    now = _now_iso()

    if resultado["outcome"] != "success":
        conn.execute(
            "UPDATE facturas_whatsapp SET estado = 'error_extraccion', error_detalle = ? WHERE id = ?",
            (resultado.get("error", "error desconocido"), factura_id),
        )
        conn.commit()
        return {"ok": False, "outcome": resultado["outcome"]}

    data = resultado["data"]
    conn.execute(
        "UPDATE facturas_whatsapp SET "
        "monto = ?, concepto = ?, cliente_nombre = ?, cliente_doc_tipo = ?, cliente_doc_nro = ?, "
        "es_nota_credito = ?, estado = 'borrador_generado', borrador_generado_at = ? WHERE id = ?",
        (
            data["monto"], data["concepto"], data["cliente_nombre"],
            data["cliente_doc_tipo"], data["cliente_doc_nro"],
            1 if data["es_nota_credito"] else 0, now, factura_id,
        ),
    )
    conn.commit()
    return {"ok": True, "monto": data["monto"], "concepto": data["concepto"], "es_nota_credito": data["es_nota_credito"]}


def list_facturas(estado: str = None) -> list:
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise FacturaError(f"Estado inválido: {estado}")
    conn = db.get_connection()
    query = "SELECT * FROM facturas_whatsapp"
    params = []
    if estado:
        query += " WHERE estado = ?"
        params.append(estado)
    query += " ORDER BY recibido_at DESC"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _get_factura(factura_id: str) -> dict:
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM facturas_whatsapp WHERE id = ?", (factura_id,)).fetchone()
    if row is None:
        raise FacturaError(f"Pedido no encontrado: {factura_id}")
    return dict(row)


def rechazar(factura_id: str, resuelto_by: str, rol: str) -> dict:
    if rol != "director":
        raise RequiereDirector("Rechazar un pedido de factura es Director-only.")
    factura = _get_factura(factura_id)
    if factura["estado"] not in ("borrador_generado", "error_extraccion"):
        raise FacturaError(f"Este pedido no se puede rechazar desde su estado actual ({factura['estado']}).")
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE facturas_whatsapp SET estado = 'rechazada', resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (now, resuelto_by, factura_id),
    )
    conn.commit()
    return {"ok": True}


def aprobar_y_emitir(
    factura_id: str, resuelto_by: str, rol: str,
    monto_final: float = None, concepto_final: str = None, factura_asociada_id: str = None,
) -> dict:
    """ESTO EMITE UNA FACTURA FISCAL REAL EN ARCA -- irreversible. Director-
    only sin excepción (a diferencia de mensajes_whatsapp, acá no hay
    equivalente a "Operativa puede si no es clínico"). `monto_final`/
    `concepto_final` opcionales: si Nicolás editó el borrador antes de
    aprobar, se usa eso en vez de lo que extrajo la IA.

    `factura_asociada_id` es OBLIGATORIO si el pedido es una nota de
    crédito (`es_nota_credito`) -- ARCA exige referenciar la factura que se
    corrige, y esa elección es de Nicolás, no de la IA (ver ai_router.py)."""
    if rol != "director":
        raise RequiereDirector("Emitir una factura real en ARCA es Director-only, sin excepción.")

    factura = _get_factura(factura_id)
    if factura["estado"] not in ("borrador_generado", "error_extraccion"):
        raise FacturaError(f"Este pedido no tiene un borrador pendiente (estado actual: {factura['estado']}).")

    monto = monto_final if monto_final is not None else factura["monto"]
    concepto = (concepto_final or factura["concepto"] or "Servicios profesionales").strip()
    if not monto or monto <= 0:
        raise FacturaError("No hay un monto válido para facturar (borrador vacío o inválido).")

    doc_tipo = factura["cliente_doc_tipo"] or 99
    doc_nro = factura["cliente_doc_nro"] or 0
    cliente_nombre = factura["cliente_nombre"]
    es_nota_credito = bool(factura["es_nota_credito"])
    tipo_cbte = TIPO_CBTE_NOTA_CREDITO if es_nota_credito else TIPO_CBTE_FACTURA

    comprobantes_asociados = None
    comprobante_asociado_texto = None
    if es_nota_credito:
        if not factura_asociada_id:
            raise FacturaError("Falta elegir a qué factura corresponde esta nota de crédito.")
        asociada = _get_factura(factura_asociada_id)
        if asociada["estado"] != "emitida":
            raise FacturaError("La factura elegida como referencia no está emitida en ARCA.")
        comprobantes_asociados = [{
            "tipo": asociada["tipo_cbte"], "pto_venta": asociada["pto_venta"], "numero": asociada["numero_cbte"],
        }]
        comprobante_asociado_texto = (
            f"Factura C {asociada['pto_venta']:04d}-{asociada['numero_cbte']:08d}"
        )

    conn = db.get_connection()
    now_iso_fecha = datetime.date.today().isoformat()

    try:
        creds = wsaa_client.obtener_credenciales(CERT_PATH, KEY_PATH)
        ultimo = wsfe_client.consultar_ultimo_autorizado(CUIT_EMISOR, creds["token"], creds["sign"], PTO_VENTA, tipo_cbte)
        numero = ultimo + 1
        resultado_cae = wsfe_client.solicitar_cae(
            CUIT_EMISOR, creds["token"], creds["sign"], PTO_VENTA, tipo_cbte, numero, monto,
            doc_tipo=doc_tipo, doc_nro=doc_nro, comprobantes_asociados=comprobantes_asociados,
        )
    except (wsaa_client.WsaaError, wsfe_client.WsfeError) as e:
        conn.execute(
            "UPDATE facturas_whatsapp SET estado = 'error_emision', error_detalle = ?, "
            "resuelto_at = ?, resuelto_by = ? WHERE id = ?",
            (str(e), _now_iso(), resuelto_by, factura_id),
        )
        conn.commit()
        raise FacturaError(f"ARCA no emitió el comprobante: {e}")

    os.makedirs(PDF_DIR, exist_ok=True)
    pdf_path = os.path.join(PDF_DIR, f"{factura_id}.pdf")
    pdf_generator.generar_pdf({
        "numero": numero, "pto_venta": PTO_VENTA, "tipo_cbte": tipo_cbte,
        "cae": resultado_cae["cae"], "vencimiento": resultado_cae["vencimiento"],
        "importe": monto, "concepto_texto": concepto, "fecha_iso": now_iso_fecha,
        "doc_tipo": doc_tipo, "doc_nro": doc_nro, "cliente_nombre": cliente_nombre,
        "comprobante_asociado_texto": comprobante_asociado_texto,
    }, pdf_path)

    conn.execute(
        "UPDATE facturas_whatsapp SET estado = 'emitida', monto = ?, concepto = ?, "
        "pto_venta = ?, tipo_cbte = ?, numero_cbte = ?, cae = ?, cae_vencimiento = ?, "
        "pdf_path = ?, factura_asociada_id = ?, resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (
            monto, concepto, PTO_VENTA, tipo_cbte, numero,
            resultado_cae["cae"], resultado_cae["vencimiento"], pdf_path, factura_asociada_id,
            _now_iso(), resuelto_by, factura_id,
        ),
    )
    conn.commit()

    # Mandar el PDF por WhatsApp es best-effort: si Twilio falla acá, el
    # comprobante YA ESTÁ EMITIDO en ARCA (no se puede deshacer) -- no tiene
    # sentido marcar error_emision por un fallo de envío. Se deja constancia
    # en el resultado para que la UI avise, pero el estado queda 'emitida'.
    envio_ok = True
    envio_error = None
    nombre_cbte = "nota de crédito" if es_nota_credito else "factura"
    try:
        twilio_client.enviar_whatsapp_con_pdf(
            factura["telefono"],
            f"Tu {nombre_cbte} C N°{PTO_VENTA:04d}-{numero:08d} está lista. CAE: {resultado_cae['cae']}.",
            pdf_path,
            factura_id,
        )
    except twilio_client.TwilioSendError as e:
        envio_ok = False
        envio_error = str(e)

    return {
        "ok": True, "cae": resultado_cae["cae"], "numero": numero, "pto_venta": PTO_VENTA,
        "pdf_path": pdf_path, "envio_whatsapp_ok": envio_ok, "envio_whatsapp_error": envio_error,
    }
