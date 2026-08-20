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
import turnos_conversacion
import twilio_client

ESTADOS_VALIDOS = {"recibido", "borrador_generado", "error_clasificacion", "aprobado_enviado", "rechazado"}

# v0.2.7 (20/08) -- pedido real de Nicolás: el acuse de un pedido de receta
# se manda solo (ver generar_borrador) -- texto fijo, no lo redacta la IA,
# para que sea siempre igual. Nunca dice nada clínico ni promete una
# receta concreta -- solo avisa que el pedido llegó y se derivó.
ACUSE_RECETA = (
    "¡Hola! 👋 Recibimos tu pedido de receta y ya lo derivamos al área correspondiente para "
    "que lo gestionen. En cuanto esté lista te avisamos por acá. ¡Gracias por tu paciencia! "
    "Consultorio Dr. Nicolás Buso."
)


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
    mensaje de un paciente por un error de la IA.

    v0.2.6 -- Fase C (turnos_conversacion.py): si el teléfono tiene una
    conversación de turno activa, este mensaje es una RESPUESTA a un
    horario ya ofrecido -- se salta la clasificación genérica y se
    interpreta directo contra esa conversación, lo que puede terminar
    creando el turno de verdad en DrApp (automático -- ver nota de diseño
    en turnos_conversacion.py, confirmada con Nicolás). Si la
    clasificación normal da turno_nuevo/cancelacion y DrApp está
    configurado, el borrador usa horarios reales en vez del texto
    genérico de la IA -- si DrApp no está configurado o falla, se usa ese
    texto genérico como respaldo, igual que siempre. En NINGÚN caso esto
    manda nada por Twilio -- el mensaje sigue esperando aprobación como
    cualquier otro (ver aprobar_y_enviar)."""
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM mensajes_whatsapp_entrantes WHERE id = ?", (mensaje_id,)).fetchone()
    if row is None:
        raise MensajeWhatsappError(f"Mensaje no encontrado: {mensaje_id}")
    now = _now_iso()

    respuesta_conversacion = turnos_conversacion.procesar_eleccion(row["telefono"], row["texto_original"], mensaje_id=mensaje_id)
    if respuesta_conversacion is not None:
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET "
            "clasificacion = 'turno_nuevo', requiere_profesional = 0, urgente = 0, borrador_respuesta = ?, "
            "accion_drapp = ?, estado = 'borrador_generado', borrador_generado_at = ? WHERE id = ?",
            (respuesta_conversacion["texto"], respuesta_conversacion["accion"], now, mensaje_id),
        )
        conn.commit()
        return {"ok": True, "clasificacion": "turno_nuevo"}

    resultado = ai_router.clasificar_y_redactar_mensaje(row["texto_original"])

    if resultado["outcome"] != "success":
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET estado = 'error_clasificacion', error_clasificacion = ? WHERE id = ?",
            (resultado.get("error", "error desconocido"), mensaje_id),
        )
        conn.commit()
        return {"ok": False, "outcome": resultado["outcome"]}

    data = resultado["data"]
    borrador_respuesta = data["borrador_respuesta"]
    accion_drapp = None

    if data["clasificacion"] == "turno_nuevo":
        ofrecido = turnos_conversacion.ofrecer_horarios(row["telefono"], row["texto_original"], mensaje_id=mensaje_id)
        if ofrecido is not None:
            borrador_respuesta = ofrecido["texto"]
            accion_drapp = ofrecido["accion"]
    elif data["clasificacion"] == "cancelacion":
        cancelado = turnos_conversacion.iniciar_cancelacion(row["telefono"], mensaje_id=mensaje_id)
        if cancelado is not None:
            borrador_respuesta = cancelado["texto"]
            accion_drapp = cancelado["accion"]
    elif data["clasificacion"] == "receta":
        # v0.2.7 (20/08) -- pedido real de Nicolás: el acuse de "recibimos
        # tu pedido" no necesita esperar aprobación -- texto fijo (no el
        # que redacta la IA, para que sea siempre igual de consistente),
        # se manda directo. La receta en sí sigue gestionándose EXACTAMENTE
        # como hasta ahora, fuera de este sistema -- esto no emite ni
        # aprueba nada clínico, solo avisa que el pedido llegó. Marianela
        # sigue viendo el pedido en la Bandeja igual que siempre.
        borrador_respuesta = ACUSE_RECETA

    # v0.2.6 (20/08) -- pedido real de Nicolás: un saludo puro ("hola",
    # "buen día", "gracias") no necesita esperar que alguien lo apruebe --
    # se manda directo, para que la respuesta sea rápida. v0.2.7 (20/08) --
    # sumado el acuse de receta (ver arriba): a diferencia del saludo, acá
    # SÍ se manda aunque requiere_profesional sea true -- lo que se manda
    # es solo el acuse, nunca una decisión clínica. Cualquier otra cosa
    # sigue el camino normal de aprobación humana. Si el envío en sí
    # falla, se cae al camino de siempre (queda como borrador para mandar
    # a mano).
    auto_enviable = accion_drapp is None and (
        (data["clasificacion"] == "ambiguo" and not data["requiere_profesional"] and not data["urgente"])
        or data["clasificacion"] == "receta"
    )
    if auto_enviable:
        try:
            twilio_client.enviar_whatsapp(row["telefono"], borrador_respuesta)
        except (twilio_client.TwilioConfigError, twilio_client.TwilioSendError):
            auto_enviable = False

    if auto_enviable:
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET "
            "clasificacion = ?, requiere_profesional = ?, urgente = ?, borrador_respuesta = ?, "
            "respuesta_final = ?, accion_drapp = ?, estado = 'aprobado_enviado', "
            "borrador_generado_at = ?, resuelto_at = ?, resuelto_by = 'sistema' WHERE id = ?",
            (
                data["clasificacion"], int(data["requiere_profesional"]), int(data["urgente"]),
                borrador_respuesta, borrador_respuesta, accion_drapp, now, now, mensaje_id,
            ),
        )
        conn.commit()
        return {"ok": True, "clasificacion": data["clasificacion"], "auto_enviado": True}

    conn.execute(
        "UPDATE mensajes_whatsapp_entrantes SET "
        "clasificacion = ?, requiere_profesional = ?, urgente = ?, borrador_respuesta = ?, "
        "accion_drapp = ?, estado = 'borrador_generado', borrador_generado_at = ? WHERE id = ?",
        (
            data["clasificacion"], int(data["requiere_profesional"]), int(data["urgente"]),
            borrador_respuesta, accion_drapp, now, mensaje_id,
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
    # v0.2.6 -- hallazgo real (21/08): rechazar un mensaje de oferta/turno
    # no cerraba la conversación de Fase C -- el próximo mensaje del mismo
    # teléfono quedaba atrapado tratando de interpretarse como una
    # elección de horario, aunque no tuviera nada que ver.
    # v0.2.7 (20/08) -- hallazgo real: sin pasar mensaje_id, esto cerraba
    # CUALQUIER conversación activa del teléfono, no solo la que este
    # mensaje representaba -- un duplicado rechazado llegó a cerrar una
    # oferta distinta ya aprobada y enviada. Con mensaje_id, solo cierra si
    # este mensaje fue el que realmente originó la conversación.
    turnos_conversacion.cerrar_conversacion_activa(mensaje["telefono"], mensaje_id=mensaje_id)
    return {"ok": True}
