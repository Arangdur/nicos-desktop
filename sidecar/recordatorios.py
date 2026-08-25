"""
Recordatorio de turnos de Medicina General por WhatsApp -- v0.2.4. Los
turnos de Psiquiatría quedan afuera a propósito porque DrApp ya los cubre
con su propio recordatorio nativo (pago aparte -- ver memoria de sesión /
NICOS_MASTER_SCOPE.md). No duplica la agenda de DrApp -- es el registro
propio de NicOS de qué recordatorio corresponde y qué pasó con cada uno,
lo mínimo necesario para no mandar un mensaje dos veces y para que
Marianela vea qué necesita su atención manual.

La carga de turnos (`importar_turnos`) es manual por ahora -- fuente de
datos deliberadamente reemplazable el día que Cormos confirme el acceso
real a su API (ver memoria del bot de WhatsApp).

Permisos (aplicados acá Y en server.py, defensa en profundidad):
- Importar turnos, completar un teléfono que faltaba: Director + Operativa
  -- carga administrativa de agenda, no clínica, Marianela la puede resolver
  sola (v0.2.6, antes era Director-only -- ver feedback real de uso: el
  calendario/recordatorios de Operativa eran de solo lectura y sin datos
  cargados, no le servían de nada).
- Ver la lista: Director + Operativa.
"""
import datetime

import secrets

import db

# Se envía cuando faltan MENOS de este tope de horas para el turno (y el
# turno todavía no pasó). No hay piso: un turno cargado con poca
# anticipación (ej. Marianela lo agenda por teléfono hoy para mañana a la
# mañana, a 15hs de distancia) se manda enseguida en el próximo tick, en
# vez de quedar 'pendiente' para siempre en silencio -- ese era un bug de
# la primera versión (auditoría 23/07). El tope holgado (25 y no 24 justo)
# también hace que el scheduler se recupere solo si el sidecar estuvo un
# rato caído: mientras el turno siga por delante, se manda una sola vez
# (después el estado deja de ser 'pendiente' y no se reconsidera).
VENTANA_ENVIO_HORAS_MAX = 25

ESTADOS_VALIDOS = {"pendiente", "enviado", "sin_telefono", "fallo_envio"}


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


class RecordatorioError(Exception):
    pass


def importar_turnos(turnos: list, created_by: str):
    """`turnos` = lista de dicts: paciente_nombre, fecha_turno (YYYY-MM-DD),
    hora_turno (HH:MM), cobertura, practica, telefono (opcional -- si falta,
    el turno entra directo en estado 'sin_telefono', nunca se asume ni se
    inventa un número)."""
    if not isinstance(turnos, list) or not turnos:
        raise RecordatorioError("No se recibió ninguna lista de turnos para importar.")

    conn = db.get_connection()
    now = _now_iso()
    creados = []
    for t in turnos:
        paciente_nombre = (t.get("paciente_nombre") or "").strip()
        fecha_turno = (t.get("fecha_turno") or "").strip()
        hora_turno = (t.get("hora_turno") or "").strip()
        if not paciente_nombre or not fecha_turno or not hora_turno:
            raise RecordatorioError(
                f"Falta paciente_nombre, fecha_turno u hora_turno en un turno: {t}"
            )
        try:
            datetime.datetime.strptime(f"{fecha_turno} {hora_turno}", "%Y-%m-%d %H:%M")
        except ValueError:
            raise RecordatorioError(
                f"Fecha/hora inválida para {paciente_nombre}: '{fecha_turno} {hora_turno}' "
                "(esperado YYYY-MM-DD y HH:MM)."
            )

        telefono = (t.get("telefono") or "").strip() or None
        estado = "pendiente" if telefono else "sin_telefono"
        recordatorio_id = secrets.token_hex(8)
        conn.execute(
            "INSERT INTO recordatorios_turnos "
            "(id, paciente_nombre, telefono, fecha_turno, hora_turno, cobertura, practica, estado, creado_at, creado_by) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                recordatorio_id, paciente_nombre, telefono, fecha_turno, hora_turno,
                (t.get("cobertura") or "").strip(), (t.get("practica") or "").strip(),
                estado, now, created_by,
            ),
        )
        creados.append(recordatorio_id)
    conn.commit()
    return {"importados": len(creados), "ids": creados}


def list_recordatorios(estado: str = None, incluir_pasados: bool = False):
    """v0.2.10 (25/08) -- hallazgo real de Nicolás: esta lista nunca filtraba
    por fecha, así que un turno de ayer se quedaba mostrado para siempre
    (el sync tampoco lo vuelve a tocar una vez que pasó -- ver
    _sincronizar_drapp_si_corresponde en worker.py, solo mira desde "hoy").
    Por default se esconden los turnos de días anteriores a hoy -- ya
    pasaron, no hay recordatorio que mandar. `incluir_pasados=True` para
    quien necesite ver el histórico completo."""
    if estado is not None and estado not in ESTADOS_VALIDOS:
        raise RecordatorioError(f"Estado inválido: {estado}")
    conn = db.get_connection()
    query = "SELECT * FROM recordatorios_turnos"
    where = []
    params = []
    if estado:
        where.append("estado = ?")
        params.append(estado)
    if not incluir_pasados:
        where.append("fecha_turno >= date('now', 'localtime')")
    if where:
        query += " WHERE " + " AND ".join(where)
    query += " ORDER BY fecha_turno, hora_turno"
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def turnos_para_enviar_ahora(ahora: datetime.datetime = None):
    """Turnos 'pendiente' (con teléfono) cuya hora real cae a menos de
    VENTANA_ENVIO_HORAS_MAX por delante de este momento (y que todavía no
    pasaron). `ahora` en HORA LOCAL -- los turnos se cargan en hora
    argentina, compararlos contra UTC corría el envío ~3hs antes de lo
    diseñado (bug encontrado en la auditoría del 23/07)."""
    ahora = ahora or datetime.datetime.now()
    conn = db.get_connection()
    rows = [dict(r) for r in conn.execute(
        "SELECT * FROM recordatorios_turnos WHERE estado = 'pendiente' AND telefono IS NOT NULL"
    ).fetchall()]

    para_enviar = []
    for r in rows:
        try:
            turno_dt = datetime.datetime.strptime(
                f"{r['fecha_turno']} {r['hora_turno']}", "%Y-%m-%d %H:%M"
            )
        except ValueError:
            continue  # dato mal cargado -- no se manda ni se rompe el scheduler, queda para revisión manual
        horas_hasta_turno = (turno_dt - ahora).total_seconds() / 3600
        if 0 < horas_hasta_turno <= VENTANA_ENVIO_HORAS_MAX:
            para_enviar.append(r)
    return para_enviar


def sincronizar_desde_drapp(turnos_drapp: list, sincronizado_by: str = "nicolas"):
    """Upsert idempotente de turnos leídos de la API de DrApp, por
    `drapp_event_id` -- cada dict de `turnos_drapp` trae paciente_nombre,
    telefono (o None), fecha_turno, hora_turno, cobertura, practica,
    drapp_event_id. Nunca toca los turnos cargados a mano (drapp_event_id
    NULL) ni reabre uno que ya se envió o falló -- evita mandar un
    recordatorio dos veces si DrApp devuelve el mismo turno en dos
    sincronizaciones seguidas.

    `sincronizado_by` default "nicolas" (no "sistema") -- creado_by tiene FK
    real contra users(user_id), y "sistema" rompía con IntegrityError apenas
    se usara sin pasar el argumento a mano (hallazgo real, ver worker.py)."""
    conn = db.get_connection()
    now = _now_iso()
    importados = 0
    actualizados = 0
    for t in turnos_drapp:
        drapp_event_id = t["drapp_event_id"]
        telefono = t.get("telefono")
        existente = conn.execute(
            "SELECT id, estado FROM recordatorios_turnos WHERE drapp_event_id = ?", (drapp_event_id,)
        ).fetchone()

        if existente is None:
            recordatorio_id = secrets.token_hex(8)
            estado = "pendiente" if telefono else "sin_telefono"
            conn.execute(
                "INSERT INTO recordatorios_turnos "
                "(id, paciente_nombre, telefono, fecha_turno, hora_turno, cobertura, practica, estado, creado_at, creado_by, drapp_event_id) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    recordatorio_id, t["paciente_nombre"], telefono, t["fecha_turno"], t["hora_turno"],
                    t.get("cobertura", ""), t.get("practica", ""), estado, now, sincronizado_by, drapp_event_id,
                ),
            )
            importados += 1
        elif existente["estado"] in ("pendiente", "sin_telefono"):
            nuevo_estado = "pendiente" if telefono else "sin_telefono"
            conn.execute(
                "UPDATE recordatorios_turnos SET paciente_nombre=?, telefono=?, fecha_turno=?, hora_turno=?, "
                "cobertura=?, practica=?, estado=? WHERE id=?",
                (
                    t["paciente_nombre"], telefono, t["fecha_turno"], t["hora_turno"],
                    t.get("cobertura", ""), t.get("practica", ""), nuevo_estado, existente["id"],
                ),
            )
            actualizados += 1
        # si ya está 'enviado' o 'fallo_envio', no se toca -- ya se resolvió.
    conn.commit()
    return {"importados": importados, "actualizados": actualizados}


def completar_telefono(recordatorio_id: str, telefono: str):
    """Para un turno que quedó 'sin_telefono' -- Marianela (o el Director)
    lo consigue después y lo completa acá, sin tener que recargar el turno
    entero de nuevo. Solo tiene sentido desde 'sin_telefono': si ya está
    'pendiente'/'enviado'/'fallo_envio' hay otra vía para corregirlo (o ya
    se resolvió), no se pisa un teléfono que ya se usó para mandar algo."""
    telefono = (telefono or "").strip()
    if not telefono:
        raise RecordatorioError("Falta el teléfono.")
    conn = db.get_connection()
    row = conn.execute("SELECT estado FROM recordatorios_turnos WHERE id = ?", (recordatorio_id,)).fetchone()
    if row is None:
        raise RecordatorioError(f"Turno no encontrado: {recordatorio_id}")
    if row["estado"] != "sin_telefono":
        raise RecordatorioError(f"Este turno no está esperando un teléfono (estado actual: {row['estado']}).")
    conn.execute(
        "UPDATE recordatorios_turnos SET telefono = ?, estado = 'pendiente' WHERE id = ?",
        (telefono, recordatorio_id),
    )
    conn.commit()
    return {"ok": True}


def marcar_resultado(recordatorio_id: str, estado: str, enviado_at: str = None):
    if estado not in ("enviado", "fallo_envio"):
        raise RecordatorioError(f"Estado de resultado inválido: {estado}")
    conn = db.get_connection()
    conn.execute(
        "UPDATE recordatorios_turnos SET estado = ?, enviado_at = ? WHERE id = ?",
        (estado, enviado_at or _now_iso(), recordatorio_id),
    )
    conn.commit()


def mensaje_recordatorio(recordatorio: dict) -> str:
    """Mensaje validado con Nicolás en la entrevista de esta sesión --
    formato fijo, no configurable por ahora."""
    fecha_turno = datetime.datetime.strptime(recordatorio["fecha_turno"], "%Y-%m-%d")
    fecha_legible = fecha_turno.strftime("%d/%m")
    primer_nombre = recordatorio["paciente_nombre"].split(",")[-1].strip().split(" ")[0]
    return (
        f"Hola {primer_nombre}! Te recordamos tu turno con el Dr. Buso "
        f"el {fecha_legible} a las {recordatorio['hora_turno']} hs. "
        "Si necesitás cancelar o reprogramar, respondé este mensaje. "
        "Consultorio Dr. Nicolás Buso."
    )
