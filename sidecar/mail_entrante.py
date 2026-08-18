"""
Bandeja de mail entrante -- v0.2.6. Mismo patrón que mensajes_whatsapp.py,
para las dos casillas de Gmail (consultorio y Abate, ver gmail_client.py):

  llega un mail -> sincronizar_casilla() lo guarda en 'recibido' -> worker
  pide a la IA clasificación + borrador -> 'borrador_generado' -> el
  Director aprueba (editado o tal cual) o rechaza -> si aprueba, ahí recién
  se manda por Gmail.

Diferencia deliberada con mensajes_whatsapp.py: acá NO hay excepción
"Operativa puede si no es clínico" -- la aprobación es SIEMPRE Director-only
(ver migración 012_mail_entrante.sql). Un mail mal contestado en nombre del
consultorio o de la Fundación es tan sensible como una factura.
"""
import datetime
import secrets

import ai_router
import db
import gmail_client

ESTADOS_VALIDOS = {"recibido", "borrador_generado", "error_clasificacion", "aprobado_enviado", "rechazado"}
CASILLAS_VALIDAS = {"consultorio", "abate"}


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


class MailEntranteError(Exception):
    pass


class RequiereDirector(MailEntranteError):
    """Aprobar/rechazar mail es siempre Director-only, sin excepción --
    server.py atrapa esto y responde 403."""
    pass


def registrar_mail_entrante(casilla: str, remitente: str, asunto: str, cuerpo: str, gmail_message_id: str = None) -> dict:
    if casilla not in CASILLAS_VALIDAS:
        raise MailEntranteError(f"Casilla inválida: {casilla!r}")
    if not remitente or not cuerpo:
        raise MailEntranteError("Falta remitente o cuerpo del mail entrante.")
    conn = db.get_connection()
    mail_id = secrets.token_hex(8)
    now = _now_iso()
    conn.execute(
        "INSERT INTO mail_entrante "
        "(id, casilla, gmail_message_id, remitente, asunto, cuerpo_original, estado, recibido_at) "
        "VALUES (?, ?, ?, ?, ?, ?, 'recibido', ?)",
        (mail_id, casilla, gmail_message_id, remitente, asunto or "", cuerpo, now),
    )
    conn.commit()
    return {"id": mail_id}


def sincronizar_casilla(casilla: str) -> dict:
    """Llamado por el worker en cada tick -- trae lo último de Gmail y
    registra solo lo que no estaba (dedupe por gmail_message_id vía el
    índice único de la migración, no hace falta un SELECT previo)."""
    mensajes = gmail_client.list_mensajes_nuevos(casilla)
    conn = db.get_connection()
    nuevos = 0
    for m in mensajes:
        existe = conn.execute(
            "SELECT 1 FROM mail_entrante WHERE casilla = ? AND gmail_message_id = ?",
            (casilla, m["gmail_message_id"]),
        ).fetchone()
        if existe:
            continue
        registrar_mail_entrante(casilla, m["remitente"], m["asunto"], m["cuerpo"], m["gmail_message_id"])
        nuevos += 1
    return {"nuevos": nuevos}


def mails_pendientes_de_borrador() -> list:
    conn = db.get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM mail_entrante WHERE estado = 'recibido' ORDER BY recibido_at"
    ).fetchall()]


def generar_borrador(mail_id: str) -> dict:
    """Un único intento, mismo criterio que mensajes_whatsapp.generar_borrador:
    si la IA falla, el mail queda en 'error_clasificacion' con el original
    intacto -- nunca se pierde ni se contesta solo."""
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM mail_entrante WHERE id = ?", (mail_id,)).fetchone()
    if row is None:
        raise MailEntranteError(f"Mail no encontrado: {mail_id}")

    resultado = ai_router.clasificar_y_redactar_mail(row["casilla"], row["asunto"], row["cuerpo_original"])
    now = _now_iso()

    if resultado["outcome"] != "success":
        conn.execute(
            "UPDATE mail_entrante SET estado = 'error_clasificacion', error_detalle = ? WHERE id = ?",
            (resultado.get("error", "error desconocido"), mail_id),
        )
        conn.commit()
        return {"ok": False, "outcome": resultado["outcome"]}

    data = resultado["data"]
    conn.execute(
        "UPDATE mail_entrante SET categoria = ?, borrador_respuesta = ?, "
        "estado = 'borrador_generado', borrador_generado_at = ? WHERE id = ?",
        (data["categoria"], data["borrador_respuesta"], now, mail_id),
    )
    conn.commit()
    return {"ok": True, "categoria": data["categoria"]}


def list_mails(casilla: str = None, estado: str = None) -> list:
    if casilla is not None and casilla not in CASILLAS_VALIDAS:
        raise MailEntranteError(f"Casilla inválida: {casilla}")
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise MailEntranteError(f"Estado inválido: {estado}")
    conn = db.get_connection()
    query = "SELECT * FROM mail_entrante"
    clauses, params = [], []
    if casilla:
        clauses.append("casilla = ?")
        params.append(casilla)
    if estado:
        clauses.append("estado = ?")
        params.append(estado)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY recibido_at DESC"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def _get_mail(mail_id: str) -> dict:
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM mail_entrante WHERE id = ?", (mail_id,)).fetchone()
    if row is None:
        raise MailEntranteError(f"Mail no encontrado: {mail_id}")
    return dict(row)


def aprobar_y_enviar(mail_id: str, resuelto_by: str, rol: str, texto_final: str = None) -> dict:
    """`rol` es siempre chequeado -- a diferencia de WhatsApp, acá no hay
    excepción para Operativa: cualquier respuesta en nombre del consultorio
    o de la Fundación necesita al Director."""
    if rol != "director":
        raise RequiereDirector("Aprobar o rechazar mail es siempre del Director.")
    mail = _get_mail(mail_id)
    if mail["estado"] not in ("borrador_generado", "error_clasificacion"):
        raise MailEntranteError(f"Este mail no tiene un borrador pendiente (estado actual: {mail['estado']}).")

    texto_a_enviar = (texto_final or mail["borrador_respuesta"] or "").strip()
    if not texto_a_enviar:
        raise MailEntranteError("No hay texto para enviar (borrador vacío y no se proveyó un texto final).")

    remitente_email = mail["remitente"]
    gmail_client.enviar_respuesta(mail["casilla"], remitente_email, mail["asunto"], texto_a_enviar, thread_id=None)

    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE mail_entrante SET estado = 'aprobado_enviado', respuesta_final = ?, "
        "resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (texto_a_enviar, now, resuelto_by, mail_id),
    )
    conn.commit()
    return {"ok": True}


def rechazar(mail_id: str, resuelto_by: str, rol: str) -> dict:
    if rol != "director":
        raise RequiereDirector("Aprobar o rechazar mail es siempre del Director.")
    mail = _get_mail(mail_id)
    if mail["estado"] not in ("borrador_generado", "error_clasificacion"):
        raise MailEntranteError(f"Este mail no se puede rechazar desde su estado actual ({mail['estado']}).")
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE mail_entrante SET estado = 'rechazado', resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (now, resuelto_by, mail_id),
    )
    conn.commit()
    return {"ok": True}
