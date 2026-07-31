"""
Bandeja de mensajes de WhatsApp ENTRANTES -- v0.2.5 (Fase B/C del diseño
original). Primera vez que NicOS recibe un WhatsApp en vez de solo mandarlos
(ver recordatorios.py, que es solo salida). El flujo es siempre:

  paciente escribe -> 'recibido' -> worker pide a la IA clasificación +
  borrador -> 'borrador_generado' -> una persona aprueba (editado o tal
  cual) o rechaza -> si aprueba, ahí recién se manda por Twilio.

La IA NUNCA manda un mensaje sola -- solo redacta. Esa es la regla de oro
de toda esta pieza, no negociable (ver ai_router.clasificar_y_redactar_mensaje
y la conversación de diseño que dio origen a esto).

Permisos (aplicados acá Y en server.py, defensa en profundidad, mismo
patrón que recordatorios.py y abate_enfermeria.py):
- Ver la bandeja: Director + Operativa.
- Aprobar/rechazar: Director + Operativa, EXCEPTO cuando `requiere_profesional`
  es true -- ahí es Director-only, mismo criterio que los scopes clinical/
  prescriptions de DrApp (esos actos exigen la identidad del profesional).
"""
import datetime
import secrets

import ai_router
import db
import twilio_client

ESTADOS_VALIDOS = {"recibido", "borrador_generado", "error_clasificacion", "aprobado_enviado", "rechazado"}


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


class MensajeWhatsappError(Exception):
    pass


class RequiereProfesional(MensajeWhatsappError):
    """El mensaje está marcado `requiere_profesional` -- solo el Director
    puede aprobarlo. server.py atrapa esto y responde 403, no 400 (no es un
    dato mal formado, es un permiso)."""
    pass


def registrar_mensaje_entrante(telefono: str, texto: str) -> dict:
    """Llamado por el handler del webhook de Twilio (server.py) apenas llega
    un mensaje real, ANTES de cualquier clasificación -- así ni un error de
    la IA puede hacer que un mensaje de un paciente se pierda sin quedar
    guardado en algún lado."""
    if not telefono or not texto:
        raise MensajeWhatsappError("Falta teléfono o texto del mensaje entrante.")
    conn = db.get_connection()
    mensaje_id = secrets.token_hex(8)
    now = _now_iso()
    conn.execute(
        "INSERT INTO mensajes_whatsapp_entrantes "
        "(id, telefono, texto_original, estado, recibido_at) VALUES (?, ?, ?, 'recibido', ?)",
        (mensaje_id, telefono, texto, now),
    )
    conn.commit()
    return {"id": mensaje_id}


def mensajes_pendientes_de_borrador() -> list:
    """Lo que el worker recorre en cada tick -- ver
    worker._procesar_mensajes_whatsapp_si_corresponde."""
    conn = db.get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT * FROM mensajes_whatsapp_entrantes WHERE estado = 'recibido' ORDER BY recibido_at"
    ).fetchall()]


def generar_borrador(mensaje_id: str) -> dict:
    """Un único intento de clasificación+borrador para este mensaje --
    `ai_router.clasificar_y_redactar_mensaje` ya trae su propio fallback
    interno (claude<->openai), así que acá no hay reintento adicional. Si
    falla igual (both_failed / auth_error), el mensaje NO se pierde: queda
    en 'error_clasificacion' con el texto original intacto y visible en la
    bandeja, para que una persona lo redacte a mano -- nunca se descarta un
    mensaje de un paciente por un error de la IA."""
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM mensajes_whatsapp_entrantes WHERE id = ?", (mensaje_id,)).fetchone()
    if row is None:
        raise MensajeWhatsappError(f"Mensaje no encontrado: {mensaje_id}")

    resultado = ai_router.clasificar_y_redactar_mensaje(row["texto_original"])
    now = _now_iso()

    if resultado["outcome"] != "success":
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET estado = 'error_clasificacion', error_clasificacion = ? WHERE id = ?",
            (resultado.get("error", "error desconocido"), mensaje_id),
        )
        conn.commit()
        return {"ok": False, "outcome": resultado["outcome"]}

    data = resultado["data"]
    conn.execute(
        "UPDATE mensajes_whatsapp_entrantes SET "
        "clasificacion = ?, requiere_profesional = ?, urgente = ?, borrador_respuesta = ?, "
        "estado = 'borrador_generado', borrador_generado_at = ? WHERE id = ?",
        (
            data["clasificacion"], int(data["requiere_profesional"]), int(data["urgente"]),
            data["borrador_respuesta"], now, mensaje_id,
        ),
    )
    conn.commit()
    return {"ok": True, "clasificacion": data["clasificacion"]}


def list_mensajes(estado: str = None) -> list:
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise MensajeWhatsappError(f"Estado inválido: {estado}")
    conn = db.get_connection()
    query = "SELECT * FROM mensajes_whatsapp_entrantes"
    params = []
    if estado:
        query += " WHERE estado = ?"
        params.append(estado)
    query += " ORDER BY urgente DESC, recibido_at DESC"
    rows = [dict(r) for r in conn.execute(query, params).fetchall()]
    for r in rows:
        # Mismo criterio conservador que aprobar_y_enviar: si la clasificación
        # falló, se muestra como si requiriera profesional -- así el frontend
        # bloquea el botón para Operativa antes de que ni siquiera lo intente.
        r["requiere_profesional"] = bool(r["requiere_profesional"]) or r["estado"] == "error_clasificacion"
        r["urgente"] = bool(r["urgente"])
    return rows


def _get_mensaje(mensaje_id: str) -> dict:
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM mensajes_whatsapp_entrantes WHERE id = ?", (mensaje_id,)).fetchone()
    if row is None:
        raise MensajeWhatsappError(f"Mensaje no encontrado: {mensaje_id}")
    d = dict(row)
    d["requiere_profesional"] = bool(d["requiere_profesional"])
    return d


def aprobar_y_enviar(mensaje_id: str, resuelto_by: str, rol: str, texto_final: str = None) -> dict:
    """`rol` es el rol de quien aprueba (server.py ya lo autenticó) -- si el
    mensaje `requiere_profesional` y el rol no es 'director', esto rechaza
    con RequiereProfesional ANTES de mandar nada. `texto_final` es opcional:
    si la persona editó el borrador antes de aprobar, se manda eso; si no,
    se manda `borrador_respuesta` tal cual."""
    mensaje = _get_mensaje(mensaje_id)
    # 'error_clasificacion' también se puede aprobar -- ahí no hay borrador de
    # la IA (falló), pero la persona puede escribir la respuesta a mano y
    # mandarla por el mismo camino, sin que el mensaje quede colgado para
    # siempre solo porque la IA falló una vez.
    if mensaje["estado"] not in ("borrador_generado", "error_clasificacion"):
        raise MensajeWhatsappError(f"Este mensaje no tiene un borrador pendiente (estado actual: {mensaje['estado']}).")
    # Si la clasificación falló, no sabemos si el mensaje es clínico o no --
    # por las dudas, se trata igual que `requiere_profesional=true` (Director-
    # only) en vez de asumir que es seguro para Operativa.
    requiere_profesional = mensaje["requiere_profesional"] or mensaje["estado"] == "error_clasificacion"
    if requiere_profesional and rol != "director":
        raise RequiereProfesional("Este mensaje toca algo clínico -- solo el Director puede aprobarlo.")

    texto_a_enviar = (texto_final or mensaje["borrador_respuesta"] or "").strip()
    if not texto_a_enviar:
        raise MensajeWhatsappError("No hay texto para enviar (borrador vacío y no se proveyó un texto final).")

    twilio_client.enviar_whatsapp(mensaje["telefono"], texto_a_enviar)

    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE mensajes_whatsapp_entrantes SET estado = 'aprobado_enviado', respuesta_final = ?, "
        "resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (texto_a_enviar, now, resuelto_by, mensaje_id),
    )
    conn.commit()
    return {"ok": True}


def rechazar(mensaje_id: str, resuelto_by: str) -> dict:
    mensaje = _get_mensaje(mensaje_id)
    if mensaje["estado"] not in ("borrador_generado", "error_clasificacion"):
        raise MensajeWhatsappError(f"Este mensaje no se puede rechazar desde su estado actual ({mensaje['estado']}).")
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE mensajes_whatsapp_entrantes SET estado = 'rechazado', resuelto_at = ?, resuelto_by = ? WHERE id = ?",
        (now, resuelto_by, mensaje_id),
    )
    conn.commit()
    return {"ok": True}
