"""
Fase C -- motor de reserva y cancelación de turnos de Medicina General por
WhatsApp. Conversación de varios mensajes con estado guardado en
turnos_conversacion (ver migraciones 013/015). Nunca inventa un horario ni
reserva/cancela nada sin que el paciente lo haya confirmado por escrito,
contra datos REALES consultados en vivo a DrApp -- ver ai_router.
interpretar_eleccion_turno, que solo puede elegir un índice de una lista
cerrada, nunca inventar un horario nuevo.

Decisión de diseño confirmada con Nicolás (21/08/2026): la ACCIÓN de
crear/cancelar el turno en DrApp es automática apenas el paciente confirma
por escrito -- el MENSAJE que se lo comunica sigue pasando SIEMPRE por la
aprobación de alguien en la Bandeja de WhatsApp, exactamente como
cualquier otro mensaje (ver mensajes_whatsapp.aprobar_y_enviar). Este
módulo nunca manda nada por Twilio directamente -- solo devuelve lo que
mensajes_whatsapp.py guarda como borrador_respuesta.

v0.2.6 (21/08) -- fallback de identificación: buscar solo por teléfono no
alcanza (un paciente puede escribirle al consultorio desde un número que
no es el que tiene cargado en DrApp). Si el teléfono no matchea a nadie,
se le pide DNI o nombre completo -- y solo se sigue adelante si eso
matchea EXACTAMENTE UN paciente (`drapp_client.buscar_pacientes_por_texto`,
fuzzy). Con 0 o 2+ resultados, se deriva a una persona -- nunca se adivina
entre pacientes con nombre parecido. Un solo intento: si tampoco identifica
a nadie así, deriva directo (no queda pidiendo datos indefinidamente).

Cada función pública devuelve `None` (nada que hacer -- DrApp no está
configurado, o no hay conversación activa; el caller usa su respaldo
genérico) o un dict `{"texto": str, "accion": None|"turno_creado"|"turno_cancelado"}`.
`accion` es None salvo que este llamado haya efectivamente creado o
cancelado un turno real en DrApp -- Marianela/Nicolás lo ven como un tag
distinto en la Bandeja (ver mensajes-whatsapp-tab.js).

Config (Ajustes -> DrApp): DRAPP_RESOURCE_ID y
DRAPP_SERVICE_KEY_MEDICINA_GENERAL -- identifican a Nicolás como recurso y
a "Medicina General / Consulta" como servicio dentro de SU cuenta de
DrApp. No hay forma genérica de inferir esto solo -- cada cuenta tiene sus
propios IDs (ver hallazgo real del 20/08: la disponibilidad mezcla varios
consultorios físicos en una sola grilla sin indicar cuál es cuál -- DrApp
resuelve el lugar solo al crear el turno, no es algo que este código
pueda ni necesite decidir).
"""
import datetime
import json
import os
import secrets

import ai_router
import db
import drapp_client

VENTANA_CANCELACION_HORAS = 24
CANTIDAD_OPCIONES_A_OFRECER = 3
DIAS_A_CONSULTAR_DISPONIBILIDAD = 14

DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def _config():
    resource_id = os.getenv("DRAPP_RESOURCE_ID")
    service_key = os.getenv("DRAPP_SERVICE_KEY_MEDICINA_GENERAL")
    if not resource_id or not service_key:
        return None
    return resource_id, service_key


def _label_legible(day: str, time: str) -> str:
    fecha = datetime.datetime.strptime(day, "%Y-%m-%d")
    return f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]} a las {time}hs"


def _sin_accion(texto: str) -> dict:
    return {"texto": texto, "accion": None}


def _primer_nombre(paciente):
    """Para saludar por el nombre una vez identificado el paciente --
    v0.2.6, pedido de Nicolás de que los mensajes sean más cálidos. None si
    no hay nombre cargado (nunca inventa un saludo con datos que no tiene)."""
    nombre = ((paciente or {}).get("firstName") or "").strip()
    return nombre.split(" ")[0] if nombre else None


def hay_conversacion_activa(telefono: str):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM turnos_conversacion WHERE telefono = ? "
        "AND estado IN ('esperando_eleccion', 'esperando_identificacion') "
        "ORDER BY creado_at DESC LIMIT 1",
        (telefono,),
    ).fetchone()
    return dict(row) if row else None


def _crear_conversacion(telefono, tipo, estado, opciones=None, eleccion_index=None):
    conn = db.get_connection()
    now = _now_iso()
    conv_id = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO turnos_conversacion "
        "(id, telefono, tipo, estado, opciones_json, eleccion_index, creado_at, actualizado_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
        (conv_id, telefono, tipo, estado, json.dumps(opciones) if opciones is not None else None, eleccion_index, now, now),
    )
    conn.commit()
    return conv_id


def _marcar_conversacion(conv_id, estado, drapp_event_id=None):
    conn = db.get_connection()
    conn.execute(
        "UPDATE turnos_conversacion SET estado = ?, drapp_event_id = ?, actualizado_at = ? WHERE id = ?",
        (estado, drapp_event_id, _now_iso(), conv_id),
    )
    conn.commit()


def _pedir_identificacion(conv_id, eleccion_index=None):
    conn = db.get_connection()
    conn.execute(
        "UPDATE turnos_conversacion SET estado = 'esperando_identificacion', eleccion_index = ?, actualizado_at = ? WHERE id = ?",
        (eleccion_index, _now_iso(), conv_id),
    )
    conn.commit()


def ofrecer_horarios(telefono: str, texto_paciente: str = None):
    """Consulta disponibilidad real y arma el texto del borrador con hasta
    `CANTIDAD_OPCIONES_A_OFRECER` opciones reales. `accion` siempre None
    acá -- ofrecer horarios nunca ejecuta nada en DrApp por sí solo. None
    (no dict) si DrApp no está configurado o la consulta falla.

    v0.2.6 (21/08) -- pedido real de Nicolás: antes siempre ofrecía los 3
    horarios más próximos, típicamente 3 seguidos del mismo día -- no
    servía para quien quería un turno para la semana que viene (frecuente
    en Psiquiatría, según él). Ahora: (1) si `texto_paciente` menciona una
    preferencia de fecha ("para la semana que viene"), se respeta desde el
    arranque -- ver ai_router.interpretar_preferencia_fecha, que nunca
    calcula una fecha ella misma, solo un número de días que se suma acá a
    datetime.now() real. (2) si no dijo nada, se ofrece UN horario por
    día distinto (no todos seguidos del mismo día) -- da variedad de
    fechas sin tener que preguntar."""
    cfg = _config()
    if cfg is None:
        return None
    resource_id, service_key = cfg

    dias_desde_hoy = 0
    if texto_paciente:
        resultado_fecha = ai_router.interpretar_preferencia_fecha(texto_paciente)
        if resultado_fecha["outcome"] == "success" and resultado_fecha["data"]["dias_desde_hoy"] is not None:
            dias_desde_hoy = resultado_fecha["data"]["dias_desde_hoy"]

    hoy = datetime.datetime.now().date()
    desde = (hoy + datetime.timedelta(days=dias_desde_hoy)).isoformat()
    hasta = (hoy + datetime.timedelta(days=dias_desde_hoy + DIAS_A_CONSULTAR_DISPONIBILIDAD)).isoformat()
    try:
        disponibilidad = drapp_client.consultar_disponibilidad(resource_id, service_key, desde, hasta)
    except (drapp_client.DrAppConfigError, drapp_client.DrAppAPIError):
        return None

    # v0.2.6 -- confirmado en vivo (20/08): la forma real es
    # {"slots": {"YYYY-MM-DD": {"HH:MM": {...}, ...}, ...}, "facets": {...}},
    # no la lista documentada en el spec ({date, resource, slots: [...]}).
    slots_por_dia = (disponibilidad or {}).get("slots", {})
    opciones = []
    for dia in sorted(slots_por_dia.keys()):
        horas = sorted(slots_por_dia[dia].keys())
        if not horas:
            continue
        # Un solo horario por día -- el primero libre de ese día -- en vez
        # de agotar las 3 opciones en el mismo día.
        opciones.append({"day": dia, "time": horas[0], "label": _label_legible(dia, horas[0])})
        if len(opciones) >= CANTIDAD_OPCIONES_A_OFRECER:
            break

    if not opciones:
        return _sin_accion(
            "¡Hola! 👋 Por ahora no tenemos horarios disponibles para Medicina General en los "
            "próximos días, pero ya avisamos al consultorio para que se comunique con vos y "
            "coordinemos. ¡Gracias por tu paciencia! Consultorio Dr. Nicolás Buso."
        )

    _crear_conversacion(telefono, tipo="turno_nuevo", estado="esperando_eleccion", opciones=opciones)

    lista = "\n".join(f"{i + 1}) {o['label']}" for i, o in enumerate(opciones))
    return _sin_accion(
        f"¡Hola! 👋 Estos son los horarios que tenemos disponibles para tu consulta de Medicina "
        f"General:\n{lista}\n\nRespondé con el número del que te quede mejor, o contame si preferís "
        "otra fecha y te busco otras opciones. Consultorio Dr. Nicolás Buso."
    )


def procesar_eleccion(telefono: str, texto: str):
    """Punto de entrada único para cualquier respuesta dentro de una
    conversación activa (elegir un horario, o dar DNI/nombre cuando se
    pidió identificación) -- despacha según el estado real de la
    conversación. None (no dict) si no hay conversación activa -- el
    caller sigue el camino normal de clasificación en ese caso."""
    conv = hay_conversacion_activa(telefono)
    if conv is None:
        return None

    if conv["estado"] == "esperando_identificacion":
        return _procesar_identificacion(conv, texto)

    return _procesar_eleccion_horario(conv, telefono, texto)


def _procesar_eleccion_horario(conv, telefono, texto):
    cfg = _config()
    if cfg is None:
        # No debería pasar (si se pudo ofrecer, DrApp estaba configurado),
        # pero por las dudas nunca se cuelga sin respuesta.
        return _sin_accion("Tuvimos un problema técnico para confirmar tu turno -- alguien del consultorio te va a contactar.")
    resource_id, service_key = cfg

    opciones = json.loads(conv["opciones_json"])
    resultado = ai_router.interpretar_eleccion_turno(opciones, texto)
    if resultado["outcome"] != "success" or resultado["data"]["eleccion"] is None:
        # v0.2.6 (21/08) -- antes de rendirse, ver si en realidad está
        # pidiendo otra fecha ("¿no tenés para la semana que viene?") en
        # vez de elegir una de las opciones ya ofrecidas -- si es así, se
        # cierra esta oferta y se vuelve a ofrecer con el rango nuevo, en
        # vez de simplemente decir "no entendí".
        resultado_fecha = ai_router.interpretar_preferencia_fecha(texto)
        if resultado_fecha["outcome"] == "success" and resultado_fecha["data"]["dias_desde_hoy"] is not None:
            _marcar_conversacion(conv["id"], "expirado")
            nueva_oferta = ofrecer_horarios(telefono, texto)
            if nueva_oferta is not None:
                return nueva_oferta
        return _sin_accion(
            "Uy, no llegué a entender bien cuál elegiste 🤔 ¿me confirmás el número de la opción, "
            "o el horario tal cual te lo mandamos?"
        )

    eleccion_index = resultado["data"]["eleccion"]
    elegido = opciones[eleccion_index]

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para confirmar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if paciente is None:
        _pedir_identificacion(conv["id"], eleccion_index=eleccion_index)
        return _sin_accion(
            "No te encuentro en el sistema con este número, pero no hay problema -- pasame tu DNI o "
            "tu nombre y apellido completo y confirmamos el turno enseguida."
        )

    return _reservar_turno(conv["id"], resource_id, service_key, paciente, elegido)


def _reservar_turno(conv_id, resource_id, service_key, paciente, elegido):
    try:
        turno = drapp_client.crear_turno(resource_id, service_key, paciente["id"], elegido["day"], elegido["time"])
    except drapp_client.DrAppConflictError:
        _marcar_conversacion(conv_id, "expirado")
        return _sin_accion(
            f"Uy, justo se ocupó el horario del {elegido['label']} mientras esperábamos tu respuesta 😅 "
            "¿querés que te busquemos otros horarios? Escribinos de nuevo pidiendo un turno."
        )
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para confirmar tu turno en el sistema -- ya avisamos al consultorio para que se comunique con vos.")

    _marcar_conversacion(conv_id, "confirmado", drapp_event_id=(turno or {}).get("id"))
    # v0.2.6 -- hallazgo real (21/08): la disponibilidad mezcla varios
    # consultorios físicos sin indicar cuál es cuál (ver ai_router/nota en
    # el docstring del módulo) -- DrApp elige el lugar en silencio al crear
    # el turno. Antes no se lo comunicaba a nadie; el evento creado SÍ trae
    # la ubicación real, así que se la agregamos a la confirmación -- si es
    # la que no le sirve al paciente, puede reaccionar de inmediato.
    ubicacion = (turno or {}).get("location") or {}
    lugar = ubicacion.get("label") or ubicacion.get("address")
    lugar_texto = f", en {lugar}" if lugar else ""
    nombre = _primer_nombre(paciente)
    saludo = f"¡Listo, {nombre}! ✅" if nombre else "¡Listo! ✅"
    return {
        "texto": f"{saludo} Tu turno quedó confirmado para el {elegido['label']}{lugar_texto}. Te esperamos con gusto. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_creado",
    }


def _procesar_identificacion(conv, texto):
    """El paciente ya mandó su DNI o nombre completo -- un solo intento:
    si no matchea a exactamente una persona, se deriva directo (nunca
    queda pidiendo datos indefinidamente ni adivina entre varias)."""
    try:
        candidatos = drapp_client.buscar_pacientes_por_texto(texto)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para confirmar quién sos -- ya avisamos al consultorio para que se comunique con vos.")

    if len(candidatos) != 1:
        _marcar_conversacion(conv["id"], "derivado")
        return _sin_accion(
            "No pude confirmar quién sos con ese dato -- no te preocupes, ya avisamos al consultorio "
            "para que se comunique con vos directamente."
        )
    paciente = candidatos[0]

    if conv["tipo"] == "cancelacion":
        return _cancelar_para_paciente(conv["id"], paciente)

    # tipo == "turno_nuevo"
    cfg = _config()
    if cfg is None:
        return _sin_accion("Tuvimos un problema técnico para confirmar tu turno -- ya avisamos al consultorio para que se comunique con vos.")
    resource_id, service_key = cfg
    opciones = json.loads(conv["opciones_json"])
    elegido = opciones[conv["eleccion_index"]]
    return _reservar_turno(conv["id"], resource_id, service_key, paciente, elegido)


def iniciar_cancelacion(telefono: str):
    """Busca el turno de Medicina General más próximo del paciente. Con
    24hs o más de anticipación, CANCELA automáticamente y devuelve
    `accion: "turno_cancelado"`. Con menos de 24hs, o si hay cualquier
    ambigüedad (0 turnos futuros, o más de uno), deriva a una persona sin
    tocar nada -- nunca cancela algo que no esté clarísimo. Psiquiatría
    queda afuera a propósito, tiene su propio manejo en DrApp. None (no
    dict) si DrApp no está configurado."""
    cfg = _config()
    if cfg is None:
        return None

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if paciente is None:
        _crear_conversacion(telefono, tipo="cancelacion", estado="esperando_identificacion")
        return _sin_accion(
            "No te encuentro en el sistema con este número, pero no hay problema -- pasame tu DNI o "
            "tu nombre y apellido completo y buscamos tu turno para cancelarlo."
        )

    return _cancelar_para_paciente(None, paciente)


def _cancelar_para_paciente(conv_id, paciente):
    try:
        turnos = drapp_client.listar_turnos_de_paciente(paciente["id"])
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    ahora = datetime.datetime.now()
    futuros_medgral = []
    for t in turnos:
        if t.get("status") != "booked":
            continue
        if "psiquiatr" in (t.get("service", {}).get("label", "") or "").lower():
            continue
        try:
            turno_dt = datetime.datetime.strptime(f"{t['day']} {t['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError, TypeError):
            continue
        if turno_dt > ahora:
            futuros_medgral.append((t, turno_dt))

    if len(futuros_medgral) == 0:
        if conv_id:
            _marcar_conversacion(conv_id, "derivado")
        return _sin_accion("No encontré ningún turno de Medicina General a tu nombre para cancelar -- ¿me confirmás la fecha, para poder ayudarte?")
    if len(futuros_medgral) > 1:
        if conv_id:
            _marcar_conversacion(conv_id, "derivado")
        return _sin_accion(
            "Veo que tenés más de un turno agendado -- para no cancelar el que no corresponde, ya "
            "avisamos al consultorio para que confirme con vos cuál es."
        )

    turno, turno_dt = futuros_medgral[0]
    label = _label_legible(turno["day"], turno["time"])
    horas_hasta = (turno_dt - ahora).total_seconds() / 3600

    if horas_hasta < VENTANA_CANCELACION_HORAS:
        if conv_id:
            _marcar_conversacion(conv_id, "derivado")
        return _sin_accion(
            f"Tu turno del {label} es en menos de 24hs -- para cancelarlo, alguien del consultorio "
            "te va a contactar directamente. ¡Gracias por avisar con tiempo!"
        )

    try:
        drapp_client.cancelar_turno(turno["id"])
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para cancelar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if conv_id:
        _marcar_conversacion(conv_id, "cancelado", drapp_event_id=turno["id"])
    nombre = _primer_nombre(paciente)
    saludo = f"Listo, {nombre} ✅" if nombre else "Listo ✅"
    return {
        "texto": f"{saludo} Cancelamos tu turno del {label}. Si querés reprogramar, escribinos cuando quieras -- va a ser un gusto ayudarte. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_cancelado",
    }
