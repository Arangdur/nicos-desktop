"""
Fase C -- motor de reserva y cancelación de turnos de Medicina General por
WhatsApp. Conversación de varios mensajes con estado guardado en
turnos_conversacion (ver migración 013). Nunca inventa un horario ni
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

Cada función pública devuelve `None` (nada que hacer -- DrApp no está
configurado, o no hay conversación activa; el caller usa su respaldo
genérico) o un dict `{"texto": str, "accion": None|"turno_creado"|"turno_cancelado"}`.
`accion` es None salvo que este llamado haya efectivamente creado o
cancelado un turno real en DrApp -- Marianela/Nicolás lo ven como un tag
distinto en la Bandeja (ver mensajes-whatsapp-tab.js) para no confundir
"esto es solo un borrador" con "esto ya pasó de verdad, esto solo avisa".

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


def hay_conversacion_activa(telefono: str):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM turnos_conversacion WHERE telefono = ? AND estado = 'esperando_eleccion' "
        "ORDER BY creado_at DESC LIMIT 1",
        (telefono,),
    ).fetchone()
    return dict(row) if row else None


def _marcar_conversacion(conv_id, estado, drapp_event_id=None):
    conn = db.get_connection()
    conn.execute(
        "UPDATE turnos_conversacion SET estado = ?, drapp_event_id = ?, actualizado_at = ? WHERE id = ?",
        (estado, drapp_event_id, _now_iso(), conv_id),
    )
    conn.commit()


def ofrecer_horarios(telefono: str):
    """Consulta disponibilidad real y arma el texto del borrador con hasta
    `CANTIDAD_OPCIONES_A_OFRECER` opciones reales. `accion` siempre None
    acá -- ofrecer horarios nunca ejecuta nada en DrApp por sí solo. None
    (no dict) si DrApp no está configurado o la consulta falla -- ver nota
    de respaldo en el docstring del módulo."""
    cfg = _config()
    if cfg is None:
        return None
    resource_id, service_key = cfg
    hoy = datetime.datetime.now().date()
    desde = hoy.isoformat()
    hasta = (hoy + datetime.timedelta(days=DIAS_A_CONSULTAR_DISPONIBILIDAD)).isoformat()
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
        for hora in sorted(slots_por_dia[dia].keys()):
            opciones.append({"day": dia, "time": hora, "label": _label_legible(dia, hora)})
            if len(opciones) >= CANTIDAD_OPCIONES_A_OFRECER:
                break
        if len(opciones) >= CANTIDAD_OPCIONES_A_OFRECER:
            break

    if not opciones:
        return _sin_accion(
            "Por ahora no encuentro horarios disponibles en los próximos días -- alguien del "
            "consultorio te va a contactar para coordinar. Consultorio Dr. Nicolás Buso."
        )

    conn = db.get_connection()
    now = _now_iso()
    conv_id = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO turnos_conversacion (id, telefono, estado, opciones_json, creado_at, actualizado_at) "
        "VALUES (?, ?, 'esperando_eleccion', ?, ?, ?)",
        (conv_id, telefono, json.dumps(opciones), now, now),
    )
    conn.commit()

    lista = "\n".join(f"{i + 1}) {o['label']}" for i, o in enumerate(opciones))
    return _sin_accion(
        f"Tenemos estos horarios disponibles para Medicina General:\n{lista}\n\n"
        "Respondé con el número del que te sirva y te confirmamos el turno. "
        "Consultorio Dr. Nicolás Buso."
    )


def procesar_eleccion(telefono: str, texto: str):
    """Interpreta la respuesta del paciente contra la conversación activa
    de ESE teléfono. Si matchea con claridad, CREA el turno de verdad en
    DrApp (automático, ver nota de diseño arriba) y devuelve
    `accion: "turno_creado"`. En cualquier otro desenlace (no se entendió,
    conflicto, paciente no encontrado, error) `accion` es None -- no pasó
    nada real, solo se le pide algo al paciente o se deriva. None (no
    dict) si no hay conversación activa -- el caller sigue el camino
    normal de clasificación en ese caso."""
    conv = hay_conversacion_activa(telefono)
    if conv is None:
        return None

    cfg = _config()
    if cfg is None:
        # No debería pasar (si se pudo ofrecer, DrApp estaba configurado),
        # pero por las dudas nunca se cuelga sin respuesta.
        return _sin_accion("Tuvimos un problema técnico para confirmar tu turno -- alguien del consultorio te va a contactar.")
    resource_id, service_key = cfg

    opciones = json.loads(conv["opciones_json"])
    resultado = ai_router.interpretar_eleccion_turno(opciones, texto)
    if resultado["outcome"] != "success" or resultado["data"]["eleccion"] is None:
        return _sin_accion(
            "No pude identificar cuál de las opciones elegiste -- ¿me confirmás el número "
            "o el horario tal cual te lo mandamos?"
        )

    elegido = opciones[resultado["data"]["eleccion"]]

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para confirmar tu turno -- alguien del consultorio te va a contactar.")

    if paciente is None:
        # Nunca se crea un paciente nuevo desde acá -- eso queda para una
        # persona, no es algo que este bot deba decidir solo.
        _marcar_conversacion(conv["id"], "derivado")
        return _sin_accion(
            "No te encuentro en el sistema del consultorio con este número -- alguien te va "
            "a contactar para coordinar el turno directamente."
        )

    try:
        turno = drapp_client.crear_turno(resource_id, service_key, paciente["id"], elegido["day"], elegido["time"])
    except drapp_client.DrAppConflictError:
        _marcar_conversacion(conv["id"], "expirado")
        return _sin_accion(
            f"Uy, justo se ocupó el horario del {elegido['label']} mientras esperábamos tu respuesta -- "
            "¿querés que te ofrezcamos otros horarios? Escribinos de nuevo pidiendo un turno."
        )
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para confirmar tu turno en el sistema -- alguien del consultorio te va a contactar.")

    _marcar_conversacion(conv["id"], "confirmado", drapp_event_id=(turno or {}).get("id"))
    return {
        "texto": f"Listo! Tu turno quedó confirmado para el {elegido['label']}. Te esperamos. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_creado",
    }


def iniciar_cancelacion(telefono: str):
    """Busca el turno de Medicina General más próximo del paciente. Con
    24hs o más de anticipación, CANCELA automáticamente (mismo criterio de
    diseño que confirmar un turno nuevo) y devuelve `accion:
    "turno_cancelado"`. Con menos de 24hs, o si hay cualquier ambigüedad (0
    turnos futuros, o más de uno), deriva a una persona sin tocar nada
    (`accion` None) -- nunca cancela algo que no esté clarísimo.
    Psiquiatría queda afuera a propósito, tiene su propio manejo en
    DrApp. None (no dict) si DrApp no está configurado."""
    cfg = _config()
    if cfg is None:
        return None

    try:
        paciente = drapp_client.buscar_paciente_por_telefono(telefono)
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- alguien del consultorio te va a contactar.")

    if paciente is None:
        return _sin_accion(
            "No te encuentro en el sistema del consultorio con este número -- alguien te va "
            "a contactar para coordinar la cancelación directamente."
        )

    try:
        turnos = drapp_client.listar_turnos_de_paciente(paciente["id"])
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para buscar tu turno -- alguien del consultorio te va a contactar.")

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
        return _sin_accion("No encontré ningún turno de Medicina General a tu nombre para cancelar -- ¿me confirmás la fecha?")
    if len(futuros_medgral) > 1:
        return _sin_accion("Tenés más de un turno agendado -- alguien del consultorio te va a contactar para confirmar cuál cancelar.")

    turno, turno_dt = futuros_medgral[0]
    label = _label_legible(turno["day"], turno["time"])
    horas_hasta = (turno_dt - ahora).total_seconds() / 3600

    if horas_hasta < VENTANA_CANCELACION_HORAS:
        return _sin_accion(
            f"Tu turno del {label} es en menos de 24hs -- para cancelarlo alguien del consultorio "
            "te va a contactar directamente."
        )

    try:
        drapp_client.cancelar_turno(turno["id"])
    except drapp_client.DrAppAPIError:
        return _sin_accion("Tuvimos un problema para cancelar tu turno -- alguien del consultorio te va a contactar.")

    return {
        "texto": f"Listo, cancelamos tu turno del {label}. Si querés reprogramar, escribinos cuando quieras. Consultorio Dr. Nicolás Buso.",
        "accion": "turno_cancelado",
    }
