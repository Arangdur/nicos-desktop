"""
Fase C -- motor de reserva y cancelación de turnos por WhatsApp (Medicina
General y, desde v0.2.7, Psiquiatría). Conversación de varios mensajes con estado guardado en
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
resuelve el lugar solo al crear el turno, no es un parámetro que
`POST /events` acepte).

v0.2.7 (20/08) -- Nicolás hace Medicina General SOLO en el consultorio de
Ordoñez y Psiquiatría SOLO en Posse (excepto una vez al mes en Ordoñez --
esa excepción queda AFUERA de este bot a propósito, se sigue coordinando
a mano). Como no se le puede pedir la ubicación a DrApp al crear el
turno, `_reservar_turno` valida la ubicación que DEVUELVE después de
crearlo -- si no es la que corresponde a la especialidad, cancela el
turno solo y deriva, en vez de confirmarle al paciente un turno en el
consultorio equivocado (se encontró un caso real así, cargado a mano
fuera de este bot).

v0.2.7 (20/08) -- Fase de Psiquiatría, decisión de diseño confirmada con
Nicolás: un "quiero un turno" que no diga cuál especialidad dispara un
menú explícito (1=Medicina General, 2=Psiquiatría, 3=otras especialidades
-- estas últimas se derivan al contacto de Stefania Rufinetto, ver
CONTACTO_OTRAS_ESPECIALIDADES, nunca se agendan acá). Si el mensaje YA
deja clara la especialidad ("turno con el psiquiatra"), se salta el menú
y va directo a ofrecer horarios de esa especialidad. Los mensajes de
Psiquiatría llevan un tono más cálido que los de rutina, y -- como
siempre en este módulo -- nunca mencionan motivo de consulta ni
diagnóstico, solo día/hora/consultorio.
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
# v0.2.6 (21/08) -- hallazgo real: rechazar el mensaje de oferta no cerraba
# la conversación -- cualquier mensaje siguiente del mismo teléfono (aunque
# no tuviera nada que ver, ej. "hola buen día") quedaba atrapado
# intentando interpretarse como una elección de horario. Se cierra
# explícitamente al rechazar (ver mensajes_whatsapp.rechazar) Y además
# expira sola después de este tiempo, por si nadie la cierra a mano.
CONVERSACION_VIGENCIA_HORAS = 4
# v0.2.7 (20/08) -- confirmado cruzando turnos reales ya reservados contra
# su dirección: "place-46ace5" es el consultorio de Ordoñez (aparece como
# "Aneit" o "NovoGen Consultorio Médico" según el turno, mismo id físico).
# Medicina General por WhatsApp NUNCA debe confirmar un turno en otro lado
# -- ver _reservar_turno.
UBICACION_ORDONEZ_ID = "place-46ace5"
# v0.2.7 (20/08) -- confirmado con Nicolás mirando la configuración real de
# horarios en DrApp: estas son las franjas de la plantilla "Aneit" -- el
# consultorio de Ordoñez que SÍ se usa para dar turnos por WhatsApp. Nunca
# miércoles/sábado/domingo. Aunque DrApp también tiene una plantilla
# "EL PUENTE" con la misma dirección (Ordoñez, 13-14hs) -- esa franja es
# para atender por demanda espontánea a trabajadores de la Usina Láctea El
# Puente, NO para turnos por WhatsApp -- a propósito NO se incluye acá (ni
# `EL PUENTE` ni su rango horario cuentan como Ordoñez para este filtro).
# Las franjas de Posse (Psiquiatría, P-SIA) no se pisan con las de Aneit en
# ningún día -- por eso alcanza con este filtro para no ofrecer nunca por
# WhatsApp un horario que en realidad va a caer en otro consultorio (o en
# el de demanda espontánea), sin tener que adivinar franja por franja
# contra la grilla mezclada de /availability (que no distingue ubicación --
# ver UBICACION_ORDONEZ_ID más arriba, que sigue como red de seguridad
# final por si esto cambia y no se actualiza).
# Claves: día de semana como datetime.date.weekday() (lunes=0).
FRANJA_ORDONEZ_MEDICINA_GENERAL = {
    0: ("10:00", "13:00"),  # lunes
    1: ("10:00", "13:00"),  # martes
    3: ("11:00", "13:00"),  # jueves
    4: ("10:00", "13:00"),  # viernes
}
# v0.2.7 (20/08) -- confirmado con Nicolás: P-SIA es 100% Psiquiatría, en
# Justiniano Posse -- sin trampa como "EL PUENTE". Franjas cruzadas contra
# turnos reales ya reservados (futuros, no los viejos de antes del cambio
# de plantilla). Nunca miércoles/sábado/domingo, y ningún día se pisa con
# las franjas de Ordoñez de arriba.
UBICACION_POSSE_ID = "place-7mtlojwwiev0sezrafw9oe"
FRANJA_POSSE_PSIQUIATRIA = {
    0: ("15:00", "18:30"),  # lunes
    1: ("08:00", "09:00"),  # martes
    3: ("15:30", "17:30"),  # jueves
    4: ("08:30", "09:30"),  # viernes
}
# v0.2.7 (20/08) -- "otras especialidades" (oftalmología, cardiología,
# ginecología, neurología, endocrinología) no se agendan acá -- se derivan
# a quien las coordina, pedido explícito de Nicolás.
CONTACTO_OTRAS_ESPECIALIDADES = "Stefania Rufinetto, +54 9 3537 60-6792"

# Config por especialidad -- qué variable de entorno trae el service key de
# DrApp, a qué consultorio corresponde, y en qué franja real. Agregar una
# especialidad nueva el día de mañana es sumar una entrada acá, no reescribir
# el resto del módulo.
ESPECIALIDADES = {
    "medicina_general": {
        "nombre": "Medicina General",
        "service_key_env": "DRAPP_SERVICE_KEY_MEDICINA_GENERAL",
        "ubicacion_id": UBICACION_ORDONEZ_ID,
        "franja": FRANJA_ORDONEZ_MEDICINA_GENERAL,
    },
    "psiquiatria": {
        "nombre": "Psiquiatría",
        "service_key_env": "DRAPP_SERVICE_KEY_PSIQUIATRIA",
        "ubicacion_id": UBICACION_POSSE_ID,
        "franja": FRANJA_POSSE_PSIQUIATRIA,
    },
}

MENU_ESPECIALIDAD = (
    "¡Hola! 👋 ¿Para qué especialidad necesitás el turno?\n"
    "1) Medicina General\n"
    "2) Psiquiatría\n"
    "3) Otros turnos (otras especialidades)\n\n"
    "Respondé con el número que corresponda."
)

DIAS_SEMANA = ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"]
MESES = [
    "enero", "febrero", "marzo", "abril", "mayo", "junio",
    "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre",
]


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def _config(especialidad: str = "medicina_general"):
    resource_id = os.getenv("DRAPP_RESOURCE_ID")
    service_key = os.getenv(ESPECIALIDADES[especialidad]["service_key_env"])
    if not resource_id or not service_key:
        return None
    return resource_id, service_key


def _drapp_activo() -> bool:
    """Chequeo mínimo para lo que no depende de una especialidad puntual
    (ej. cancelación, que busca contra TODOS los turnos del paciente sin
    necesitar ningún service key en particular)."""
    return bool(os.getenv("DRAPP_RESOURCE_ID"))


def _label_legible(day: str, time: str) -> str:
    fecha = datetime.datetime.strptime(day, "%Y-%m-%d")
    return f"{DIAS_SEMANA[fecha.weekday()]} {fecha.day} de {MESES[fecha.month - 1]} a las {time}hs"


def _sin_accion(texto: str) -> dict:
    return {"texto": texto, "accion": None}


def _en_franja(especialidad: str, day: str, time: str) -> bool:
    """True si ese día/horario cae dentro de la franja real de esa
    especialidad (ver ESPECIALIDADES) -- comparación de strings alcanza
    porque HH:MM siempre viene con cero a la izquierda."""
    franja = ESPECIALIDADES[especialidad]["franja"].get(datetime.date.fromisoformat(day).weekday())
    if franja is None:
        return False
    desde, hasta = franja
    return desde <= time < hasta


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
        "AND estado IN ('esperando_especialidad', 'esperando_eleccion', 'esperando_identificacion') "
        "ORDER BY creado_at DESC LIMIT 1",
        (telefono,),
    ).fetchone()
    if row is None:
        return None
    conv = dict(row)
    creado = datetime.datetime.fromisoformat(conv["creado_at"])
    if (datetime.datetime.utcnow() - creado).total_seconds() > CONVERSACION_VIGENCIA_HORAS * 3600:
        _marcar_conversacion(conv["id"], "expirado")
        return None
    return conv


def cerrar_conversacion_activa(telefono: str, mensaje_id: str = None):
    """Cierra la conversación de turno activa para este teléfono -- se llama
    al rechazar un mensaje (ver mensajes_whatsapp.rechazar) para que un
    paciente cuya OFERTA se rechazó (nunca la llegó a ver) no quede
    atrapado. No-op si no hay ninguna activa.

    v0.2.7 (20/08) -- hallazgo real: la conversación es por teléfono, no
    por mensaje -- rechazar un mensaje CUALQUIERA de ese teléfono (ej. un
    duplicado que la IA no supo interpretar) cerraba de rebote una
    conversación activa distinta, ya aprobada y enviada, dejando al
    paciente sin poder responder "1/2/3". Si se pasa `mensaje_id`, solo
    cierra cuando ese mensaje es efectivamente el que originó la
    conversación (`mensaje_origen_id`) -- si no coincide, o si la
    conversación no tiene origen registrado, no toca nada (la expiración
    automática por tiempo sigue como red de seguridad, ver
    CONVERSACION_VIGENCIA_HORAS). Sin `mensaje_id` (ningún caller actual lo
    omite, queda por compatibilidad) cierra sin esa validación."""
    conv = hay_conversacion_activa(telefono)
    if conv is None:
        return
    if mensaje_id is not None and conv.get("mensaje_origen_id") != mensaje_id:
        return
    _marcar_conversacion(conv["id"], "cancelado")


def _crear_conversacion(telefono, tipo, estado, opciones=None, eleccion_index=None, mensaje_id=None, especialidad=None, drapp_event_id=None, consumer_id=None):
    conn = db.get_connection()
    now = _now_iso()
    conv_id = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO turnos_conversacion "
        "(id, telefono, tipo, estado, especialidad, opciones_json, eleccion_index, drapp_event_id, consumer_id, mensaje_origen_id, creado_at, actualizado_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (conv_id, telefono, tipo, estado, especialidad, json.dumps(opciones) if opciones is not None else None, eleccion_index, drapp_event_id, consumer_id, mensaje_id, now, now),
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


def ofrecer_horarios(telefono: str, texto_paciente: str = None, mensaje_id: str = None):
    """Punto de entrada público para "quiero un turno". v0.2.7 (20/08),
    Fase de Psiquiatría: primero hay que saber la especialidad -- si
    `texto_paciente` ya la deja clara ("turno con el psiquiatra"), se salta
    directo a ofrecer esa especialidad (`_ofrecer_horarios_especialidad`);
    si menciona otra especialidad que el consultorio deriva (oftalmología,
    cardiología, etc.), responde con el contacto correspondiente; si no
    queda claro, pregunta con el menú explícito (`MENU_ESPECIALIDAD`) antes
    de mostrar cualquier horario -- decisión de diseño confirmada con
    Nicolás, no asumir Medicina General por default. None (no dict) si
    DrApp no está configurado en absoluto."""
    if not _drapp_activo():
        return None

    especialidad = None
    if texto_paciente:
        resultado_esp = ai_router.interpretar_especialidad(texto_paciente)
        if resultado_esp["outcome"] == "success":
            especialidad = resultado_esp["data"]["especialidad"]

    if especialidad == "otras_especialidades":
        return _sin_accion(_texto_derivar_otras_especialidades())
    if especialidad in ESPECIALIDADES:
        return _ofrecer_horarios_especialidad(especialidad, telefono, texto_paciente, mensaje_id)

    _crear_conversacion(telefono, tipo="turno_nuevo", estado="esperando_especialidad", mensaje_id=mensaje_id)
    return _sin_accion(MENU_ESPECIALIDAD)


def _texto_derivar_otras_especialidades() -> str:
    return (
        f"¡Hola! 👋 Para esa especialidad coordina turnos {CONTACTO_OTRAS_ESPECIALIDADES} -- "
        "escribile directo y te va a ayudar. ¡Gracias por escribirnos!"
    )


def _ofrecer_horarios_especialidad(
    especialidad: str, telefono: str, texto_paciente: str = None, mensaje_id: str = None,
    tipo: str = "turno_nuevo", drapp_event_id: str = None, turno_actual_label: str = None,
    consumer_id: str = None,
):
    """Consulta disponibilidad real y arma el texto del borrador con hasta
    `CANTIDAD_OPCIONES_A_OFRECER` opciones reales, ya para una especialidad
    concreta (`ESPECIALIDADES`). `accion` siempre None acá -- ofrecer
    horarios nunca ejecuta nada en DrApp por sí solo. None (no dict) si
    esa especialidad no está configurada o la consulta falla.

    v0.2.6 (21/08) -- pedido real de Nicolás: antes siempre ofrecía los 3
    horarios más próximos, típicamente 3 seguidos del mismo día -- no
    servía para quien quería un turno para la semana que viene (frecuente
    en Psiquiatría, según él). Ahora: (1) si `texto_paciente` menciona una
    preferencia de fecha ("para la semana que viene"), se respeta desde el
    arranque -- ver ai_router.interpretar_preferencia_fecha, que nunca
    calcula una fecha ella misma, solo un número de días que se suma acá a
    datetime.now() real. (2) si no dijo nada, se ofrece UN horario por
    día distinto (no todos seguidos del mismo día) -- da variedad de
    fechas sin tener que preguntar.

    v0.2.8 (24/08) -- reusada también para reprogramación (`tipo`,
    `drapp_event_id` del turno VIEJO a mover, `turno_actual_label` para
    avisarle al paciente cuál está reemplazando) -- la mecánica de
    consultar/filtrar/armar opciones es idéntica, solo cambia qué tipo de
    conversación se abre y el texto del mensaje. `consumer_id`: si quien
    llama YA identificó al paciente (ej. reprogramación que necesitó DNI
    para encontrar el turno viejo), se guarda acá para que
    `_procesar_eleccion_horario` no vuelva a intentar `buscar_paciente_
    por_telefono` al confirmar -- ese teléfono nunca va a matchear (por
    eso hizo falta pedir DNI en primer lugar), y sin esto quedaba pidiendo
    identificación en loop."""
    cfg = _config(especialidad)
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
        # v0.2.6 (20/08) -- hallazgo real: DrApp devuelve en la grilla TODOS
        # los horarios del día, ocupados o no -- "capacity" es lo que dice
        # si de verdad hay lugar (0 = sin lugar, negativo = anómalo/
        # bloqueado, >=1 = libre). Antes se tomaba el primer horario de la
        # lista sin mirar esto, y se llegó a ofrecer -- y crear -- un turno
        # que ya estaba reservado por otro paciente en otro consultorio.
        # v0.2.7 (20/08) -- además, la grilla mezcla los consultorios sin
        # indicar cuál es cuál -- se filtra también por la franja horaria
        # real de esta especialidad (ver ESPECIALIDADES) para no ofrecer
        # nunca un horario que en realidad es de otro consultorio.
        horas_libres = sorted(
            h for h, s in slots_por_dia[dia].items()
            if (s or {}).get("capacity", 0) > 0 and _en_franja(especialidad, dia, h)
        )
        if not horas_libres:
            continue
        # Un solo horario por día -- el primero libre de ese día -- en vez
        # de agotar las 3 opciones en el mismo día.
        opciones.append({"day": dia, "time": horas_libres[0], "label": _label_legible(dia, horas_libres[0])})
        if len(opciones) >= CANTIDAD_OPCIONES_A_OFRECER:
            break

    nombre_especialidad = ESPECIALIDADES[especialidad]["nombre"]
    if not opciones:
        return _sin_accion(
            f"¡Hola! 👋 Por ahora no tenemos horarios disponibles para {nombre_especialidad} en los "
            "próximos días, pero ya avisamos al consultorio para que se comunique con vos y "
            "coordinemos. ¡Gracias por tu paciencia! Consultorio Dr. Nicolás Buso."
        )

    _crear_conversacion(
        telefono, tipo=tipo, estado="esperando_eleccion", opciones=opciones,
        mensaje_id=mensaje_id, especialidad=especialidad, drapp_event_id=drapp_event_id,
        consumer_id=consumer_id,
    )

    # v0.2.7 (20/08) -- pedido real de Nicolás: los mensajes de Psiquiatría
    # llevan un tono más cálido que un turno de rutina.
    saludo = "¡Hola! 🤍 Quiero ayudarte a encontrar un buen horario." if especialidad == "psiquiatria" else "¡Hola! 👋"
    lista = "\n".join(f"{i + 1}) {o['label']}" for i, o in enumerate(opciones))
    if tipo == "reprogramacion":
        intro = f"{saludo} Para reprogramar tu turno del {turno_actual_label}, estos son los horarios nuevos disponibles para {nombre_especialidad}:"
    else:
        intro = f"{saludo} Estos son los horarios que tenemos disponibles para tu consulta de {nombre_especialidad}:"
    return _sin_accion(
        f"{intro}\n{lista}\n\nRespondé con el número del que te quede mejor, o "
        "contame si preferís otra fecha y te busco otras opciones. Consultorio Dr. Nicolás Buso."
    )


def procesar_eleccion(telefono: str, texto: str, mensaje_id: str = None):
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
    if conv["estado"] == "esperando_especialidad":
        return _procesar_especialidad(conv, telefono, texto, mensaje_id=mensaje_id)

    return _procesar_eleccion_horario(conv, telefono, texto, mensaje_id=mensaje_id)


def _procesar_especialidad(conv, telefono, texto, mensaje_id=None):
    """El paciente ya recibió el menú (MENU_ESPECIALIDAD) -- interpreta la
    respuesta (número o texto libre, mismo clasificador que detecta la
    especialidad en un mensaje inicial) y despacha a la oferta real, o
    deriva si pidió otra especialidad."""
    resultado = ai_router.interpretar_especialidad(texto)
    especialidad = resultado["data"]["especialidad"] if resultado["outcome"] == "success" else None

    if especialidad == "otras_especialidades":
        _marcar_conversacion(conv["id"], "derivado")
        return _sin_accion(_texto_derivar_otras_especialidades())
    if especialidad in ESPECIALIDADES:
        # Esta conversación (esperando_especialidad) ya cumplió su función
        # -- la oferta real abre una conversación nueva, propia de esa
        # especialidad (ver _ofrecer_horarios_especialidad).
        _marcar_conversacion(conv["id"], "expirado")
        nueva_oferta = _ofrecer_horarios_especialidad(especialidad, telefono, texto, mensaje_id)
        if nueva_oferta is not None:
            return nueva_oferta
        return _sin_accion("Tuvimos un problema técnico para mostrarte los horarios -- alguien del consultorio te va a contactar.")

    return _sin_accion(
        "Uy, no entendí bien cuál opción elegiste 🤔 respondé con 1 (Medicina General), "
        "2 (Psiquiatría) o 3 (otros turnos)."
    )


def _procesar_eleccion_horario(conv, telefono, texto, mensaje_id=None):
    especialidad = conv["especialidad"] or "medicina_general"
    cfg = _config(especialidad)
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
            # v0.2.7 (20/08) -- llama a la versión YA acotada a esta
            # especialidad, no al punto de entrada público -- si llamara a
            # ofrecer_horarios() de nuevo, volvería a detectar la
            # especialidad desde este mensaje puntual (que puede no
            # mencionarla, ej. "¿no tenés para la semana que viene?") y
            # perdería la especialidad ya sabida, cayendo otra vez en el
            # menú 1/2/3.
            nueva_oferta = _ofrecer_horarios_especialidad(especialidad, telefono, texto, mensaje_id)
            if nueva_oferta is not None:
                return nueva_oferta
        # v0.2.6 (20/08) -- pedido real de Nicolás: si ni es una elección ni
        # una preferencia de fecha, probablemente sea un saludo o charla
        # mínima ("hola", "gracias") -- en vez de la línea fija de "no
        # entendí", que la IA redacte algo natural (mismo clasificador que
        # usa cualquier mensaje sin conversación activa). La conversación
        # sigue abierta -- si después responde con el número, se resuelve
        # igual. Si la IA falla, la línea fija de siempre es el respaldo.
        redactado = ai_router.clasificar_y_redactar_mensaje(texto)
        if redactado["outcome"] == "success":
            return _sin_accion(redactado["data"]["borrador_respuesta"])
        return _sin_accion(
            "Uy, no llegué a entender bien cuál elegiste 🤔 ¿me confirmás el número de la opción, "
            "o el horario tal cual te lo mandamos?"
        )

    eleccion_index = resultado["data"]["eleccion"]
    elegido = opciones[eleccion_index]

    if conv["consumer_id"]:
        # v0.2.8 (24/08) -- hallazgo real (test): si esta conversación ya
        # arrancó con el paciente identificado (ej. reprogramación que
        # necesitó DNI para encontrar el turno viejo), no tiene sentido
        # volver a intentar por teléfono -- ese número nunca va a matchear
        # (por eso hizo falta identificarlo en primer lugar), y sin este
        # atajo quedaba pidiendo DNI en loop cada vez que confirmaba algo.
        paciente = {"id": conv["consumer_id"]}
    else:
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

    if conv["tipo"] == "reprogramacion":
        return _confirmar_reprogramacion(conv, especialidad, paciente, elegido)
    return _reservar_turno(conv["id"], especialidad, resource_id, service_key, paciente, elegido)


def _reservar_turno(conv_id, especialidad, resource_id, service_key, paciente, elegido):
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

    ubicacion = (turno or {}).get("location") or {}

    # v0.2.7 (20/08) -- red de seguridad, hallazgo real: cada especialidad
    # es SOLO en su consultorio (ver ESPECIALIDADES), pero DrApp elige el
    # consultorio en silencio al crear el turno (no es un parámetro que se
    # le pueda pedir) -- ya se encontró un turno real cargado a mano en el
    # consultorio equivocado. Si el que asignó no es el que corresponde a
    # esta especialidad, se cancela solo y se deriva -- nunca se le
    # confirma al paciente un turno en el lugar que no le corresponde.
    ubicacion_esperada = ESPECIALIDADES[especialidad]["ubicacion_id"]
    if ubicacion.get("id") and ubicacion["id"] != ubicacion_esperada:
        try:
            drapp_client.cancelar_turno(turno["id"])
        except drapp_client.DrAppAPIError:
            pass  # de todas formas no se lo confirmamos al paciente
        _marcar_conversacion(conv_id, "derivado")
        return _sin_accion(
            "Uy, tuvimos un problema para confirmar tu turno en el consultorio correcto 😅 "
            "ya avisamos al consultorio para que se comunique con vos y lo resolvamos enseguida."
        )

    _marcar_conversacion(conv_id, "confirmado", drapp_event_id=(turno or {}).get("id"))
    # v0.2.6 -- hallazgo real (21/08): la disponibilidad mezcla varios
    # consultorios físicos sin indicar cuál es cuál (ver ai_router/nota en
    # el docstring del módulo) -- DrApp elige el lugar en silencio al crear
    # el turno. Antes no se lo comunicaba a nadie; el evento creado SÍ trae
    # la ubicación real, así que se la agregamos a la confirmación -- si es
    # la que no le sirve al paciente, puede reaccionar de inmediato.
    lugar = ubicacion.get("label") or ubicacion.get("address")
    lugar_texto = f", en {lugar}" if lugar else ""
    nombre = _primer_nombre(paciente)
    # v0.2.7 (20/08) -- pedido real de Nicolás: tono más cálido en Psiquiatría.
    emoji = "🤍" if especialidad == "psiquiatria" else "✅"
    saludo = f"¡Listo, {nombre}! {emoji}" if nombre else f"¡Listo! {emoji}"
    return {
        "texto": f"{saludo} Tu turno quedó confirmado para el {elegido['label']}{lugar_texto}. Te esperamos con gusto. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_creado",
    }


def _confirmar_reprogramacion(conv, especialidad, paciente, elegido):
    """v0.2.8 (24/08) -- pedido real de Nicolás: reprogramar un turno en un
    solo paso (antes había que cancelar y pedir uno nuevo aparte). Mueve el
    turno VIEJO (`conv["drapp_event_id"]`, guardado desde que se ofrecieron
    los horarios nuevos) al día/hora elegidos -- DrApp lo actualiza en el
    lugar, no crea un evento nuevo."""
    event_id = conv["drapp_event_id"]
    try:
        turno = drapp_client.reprogramar_turno(event_id, elegido["day"], elegido["time"])
    except drapp_client.DrAppConflictError:
        _marcar_conversacion(conv["id"], "expirado")
        return _sin_accion(
            f"Uy, justo se ocupó el horario del {elegido['label']} mientras esperábamos tu respuesta 😅 "
            "¿querés que te busquemos otros horarios? Escribinos de nuevo pidiendo reprogramar."
        )
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para reprogramar tu turno en el sistema -- ya avisamos al consultorio para que se comunique con vos.")

    ubicacion = (turno or {}).get("location") or {}
    ubicacion_esperada = ESPECIALIDADES[especialidad]["ubicacion_id"]
    if ubicacion.get("id") and ubicacion["id"] != ubicacion_esperada:
        # v0.2.8 (24/08) -- misma red de seguridad que crear un turno nuevo
        # (ver _reservar_turno) -- pero acá NO se cancela nada: el turno ya
        # se movió de verdad en DrApp, solo quedó en el consultorio que no
        # corresponde. Revertirlo agregaría más riesgo que dejarlo para que
        # una persona lo resuelva a mano -- se deriva, no se le confirma
        # nada al paciente hasta que esté resuelto.
        _marcar_conversacion(conv["id"], "derivado")
        return _sin_accion(
            "Uy, tuvimos un problema para reprogramar tu turno en el consultorio correcto 😅 "
            "ya avisamos al consultorio para que se comunique con vos y lo resolvamos enseguida."
        )

    _marcar_conversacion(conv["id"], "confirmado", drapp_event_id=event_id)
    lugar = ubicacion.get("label") or ubicacion.get("address")
    lugar_texto = f", en {lugar}" if lugar else ""
    nombre = _primer_nombre(paciente)
    emoji = "🤍" if especialidad == "psiquiatria" else "✅"
    saludo = f"¡Listo, {nombre}! {emoji}" if nombre else f"¡Listo! {emoji}"
    return {
        "texto": f"{saludo} Reprogramamos tu turno para el {elegido['label']}{lugar_texto}. Te esperamos con gusto. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_reprogramado",
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

    if conv["tipo"] == "reprogramacion":
        if conv["eleccion_index"] is not None:
            # Ya había elegido el horario nuevo antes de que hiciera falta
            # identificarlo (ver _procesar_eleccion_horario) -- se retoma
            # esa elección en vez de volver a preguntar.
            especialidad = conv["especialidad"] or "medicina_general"
            opciones = json.loads(conv["opciones_json"])
            elegido = opciones[conv["eleccion_index"]]
            return _confirmar_reprogramacion(conv, especialidad, paciente, elegido)
        # Todavía no sabíamos ni cuál es su turno actual -- recién ahora,
        # identificado, se lo busca (misma conversación que en
        # iniciar_reprogramacion cuando el teléfono no matcheaba).
        _marcar_conversacion(conv["id"], "expirado")
        return _iniciar_reprogramacion_para_paciente(conv["telefono"], paciente)

    # tipo == "turno_nuevo"
    especialidad = conv["especialidad"] or "medicina_general"
    cfg = _config(especialidad)
    if cfg is None:
        return _sin_accion("Tuvimos un problema técnico para confirmar tu turno -- ya avisamos al consultorio para que se comunique con vos.")
    resource_id, service_key = cfg
    opciones = json.loads(conv["opciones_json"])
    elegido = opciones[conv["eleccion_index"]]
    return _reservar_turno(conv["id"], especialidad, resource_id, service_key, paciente, elegido)


def _especialidad_de_label(service_label: str):
    """Mapea el service.label real de DrApp a nuestra clave interna de
    especialidad -- None si es algo que este bot no maneja (ej. una de las
    'otras especialidades' que coordina Stefania, si comparten cuenta de
    DrApp). Se usa para no cancelar por error un turno que no es de
    Medicina General ni de Psiquiatría."""
    label = (service_label or "").lower()
    if "psiquiatr" in label:
        return "psiquiatria"
    if "medicina general" in label:
        return "medicina_general"
    return None


def _turnos_futuros_del_paciente(paciente) -> list:
    """Turnos futuros (booked, Medicina General o Psiquiatría) de este
    paciente, como lista de (turno, turno_dt, especialidad) -- compartido
    entre cancelación y reprogramación, las dos necesitan encontrar
    exactamente UN turno futuro antes de tocar nada. Puede levantar
    drapp_client.DrAppAPIError -- lo maneja quien llama."""
    turnos = drapp_client.listar_turnos_de_paciente(paciente["id"])
    ahora = datetime.datetime.now()
    futuros = []
    for t in turnos:
        if t.get("status") != "booked":
            continue
        # v0.2.7 (20/08) -- solo Medicina General/Psiquiatría -- cualquier
        # otra especialidad (si comparte cuenta de DrApp) se ignora, este
        # bot no la gestiona.
        especialidad = _especialidad_de_label((t.get("service") or {}).get("label"))
        if especialidad is None:
            continue
        try:
            turno_dt = datetime.datetime.strptime(f"{t['day']} {t['time']}", "%Y-%m-%d %H:%M")
        except (KeyError, ValueError, TypeError):
            continue
        if turno_dt > ahora:
            futuros.append((t, turno_dt, especialidad))
    return futuros


def iniciar_reprogramacion(telefono: str, mensaje_id: str = None):
    """v0.2.8 (24/08) -- pedido real de Nicolás: reprogramar en un solo
    paso, en vez de cancelar y tener que pedir un turno nuevo aparte.
    Busca el único turno futuro del paciente y ofrece horarios nuevos de
    la MISMA especialidad -- misma ventana de 24hs y mismos criterios de
    ambigüedad que cancelación (nunca toca un turno que no esté
    clarísimo). None (no dict) si DrApp no está configurado."""
    if not _drapp_activo():
        return None

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if paciente is None:
        _crear_conversacion(telefono, tipo="reprogramacion", estado="esperando_identificacion", mensaje_id=mensaje_id)
        return _sin_accion(
            "No te encuentro en el sistema con este número, pero no hay problema -- pasame tu DNI o "
            "tu nombre y apellido completo y buscamos tu turno para reprogramarlo."
        )

    return _iniciar_reprogramacion_para_paciente(telefono, paciente, mensaje_id=mensaje_id)


def _iniciar_reprogramacion_para_paciente(telefono, paciente, mensaje_id=None):
    try:
        futuros = _turnos_futuros_del_paciente(paciente)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if len(futuros) == 0:
        return _sin_accion("No encontré ningún turno a tu nombre para reprogramar -- ¿me confirmás la fecha, para poder ayudarte?")
    if len(futuros) > 1:
        return _sin_accion(
            "Veo que tenés más de un turno agendado -- para no reprogramar el que no corresponde, ya "
            "avisamos al consultorio para que confirme con vos cuál es."
        )

    turno, turno_dt, especialidad = futuros[0]
    label_actual = _label_legible(turno["day"], turno["time"])
    horas_hasta = (turno_dt - datetime.datetime.now()).total_seconds() / 3600

    if horas_hasta < VENTANA_CANCELACION_HORAS:
        return _sin_accion(
            f"Tu turno del {label_actual} es en menos de 24hs -- para reprogramarlo, alguien del "
            "consultorio te va a contactar directamente. ¡Gracias por avisar con tiempo!"
        )

    ofrecido = _ofrecer_horarios_especialidad(
        especialidad, telefono, mensaje_id=mensaje_id,
        tipo="reprogramacion", drapp_event_id=turno["id"], turno_actual_label=label_actual,
        consumer_id=paciente["id"],
    )
    if ofrecido is None:
        return _sin_accion("Tuvimos un problema técnico para reprogramar tu turno -- alguien del consultorio te va a contactar.")
    return ofrecido


def iniciar_cancelacion(telefono: str, mensaje_id: str = None):
    """Busca el turno (Medicina General o Psiquiatría) más próximo del
    paciente. Con 24hs o más de anticipación, CANCELA automáticamente y
    devuelve `accion: "turno_cancelado"`. Con menos de 24hs, o si hay
    cualquier ambigüedad (0 turnos futuros, o más de uno), deriva a una
    persona sin tocar nada -- nunca cancela algo que no esté clarísimo.
    None (no dict) si DrApp no está configurado."""
    if not _drapp_activo():
        return None

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    if paciente is None:
        _crear_conversacion(telefono, tipo="cancelacion", estado="esperando_identificacion", mensaje_id=mensaje_id)
        return _sin_accion(
            "No te encuentro en el sistema con este número, pero no hay problema -- pasame tu DNI o "
            "tu nombre y apellido completo y buscamos tu turno para cancelarlo."
        )

    return _cancelar_para_paciente(None, paciente)


def _cancelar_para_paciente(conv_id, paciente):
    try:
        futuros = _turnos_futuros_del_paciente(paciente)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- ya avisamos al consultorio para que se comunique con vos.")

    ahora = datetime.datetime.now()
    if len(futuros) == 0:
        if conv_id:
            _marcar_conversacion(conv_id, "derivado")
        return _sin_accion("No encontré ningún turno a tu nombre para cancelar -- ¿me confirmás la fecha, para poder ayudarte?")
    if len(futuros) > 1:
        if conv_id:
            _marcar_conversacion(conv_id, "derivado")
        return _sin_accion(
            "Veo que tenés más de un turno agendado -- para no cancelar el que no corresponde, ya "
            "avisamos al consultorio para que confirme con vos cuál es."
        )

    turno, turno_dt, especialidad = futuros[0]
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
    emoji = "🤍" if especialidad == "psiquiatria" else "✅"
    saludo = f"Listo, {nombre} {emoji}" if nombre else f"Listo {emoji}"
    return {
        "texto": f"{saludo} Cancelamos tu turno del {label}. Si querés reprogramar, escribinos cuando quieras -- va a ser un gusto ayudarte. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_cancelado",
    }


def list_conversaciones_recientes(horas: int = 24) -> list:
    """v0.2.7 (20/08) -- pedido real de Nicolás: panel de salud de Fase C en
    el Resumen del Director. Antes, la única forma de ver el estado de una
    conversación de turno era una query manual a SQLite -- esto expone lo
    mismo por API para armar un panel: conversaciones activas ahora
    mismo, y las que quedaron 'derivado'/'expirado' en las últimas
    `horas` (necesitan revisión humana, o son solo ruido esperable --
    quien mira el panel decide). Trae todo lo creado O actualizado en la
    ventana, para no perder una conversación vieja que recién ahora
    quedó derivada."""
    conn = db.get_connection()
    limite = (datetime.datetime.utcnow() - datetime.timedelta(hours=horas)).isoformat()
    rows = conn.execute(
        "SELECT id, telefono, tipo, estado, especialidad, creado_at, actualizado_at "
        "FROM turnos_conversacion WHERE creado_at >= ? OR actualizado_at >= ? "
        "ORDER BY actualizado_at DESC",
        (limite, limite),
    ).fetchall()
    return [dict(r) for r in rows]
