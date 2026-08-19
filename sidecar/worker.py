"""
Worker loop durable de NicOS Core — reemplaza los `threading.Thread` ad-hoc por
request (v0.2) por un único loop persistente que reencola desde SQLite. Esto es
lo que hace que una tarea sobreviva a que el proceso se reinicie a mitad de
camino (crash, `kill -9`, apagón de la Mac): el estado en `tasks.state` ES la
cola, no hace falta una cola separada — el worker simplemente vuelve a
preguntarle a la base qué hay pendiente en su próximo ciclo, sin depender de
que un thread efímero haya sobrevivido.

`recover_orphaned_tasks()` corre una sola vez al arrancar, antes del loop:
decide qué hacer con tareas que quedaron en un estado intermedio de una
corrida anterior. Desde v0.2.1-rc1, para tareas interrumpidas en 'executing'
no asume nada -- reconcilia contra el ledger durable de registrar_movimiento.py
(ver centro_mando_adapter.reconcile_execution_attempt) para saber si el
movimiento se llegó a registrar o no, en vez de mandar siempre a needs_review
con un mensaje genérico.
"""
import datetime
import json
import os
import sys
import time
import traceback
import uuid

import ai_router
import centro_mando_adapter
import db
import drapp_client
import facturas
import gmail_client
import mail_entrante
import mensajes_whatsapp
import recordatorios
import tasks
import twilio_client

POLL_INTERVAL_SECONDS = 1
QUEUED_STATES = ("received", "parsing", "ready")

# Cada cuánto se revisa si hay recordatorios de turno para mandar -- no hace
# falta que sea cada segundo como el resto del loop, ver
# recordatorios.turnos_para_enviar_ahora() para la banda de 23-25hs que
# tolera que el chequeo no caiga exacto.
RECORDATORIOS_CHECK_INTERVAL_SECONDS = 300
_ultimo_chequeo_recordatorios = None

# Sincronización automática de turnos desde la API de DrApp -- opt-in: si
# DRAPP_API_KEY no está configurado, esta función es un no-op y el
# formulario manual del Director sigue siendo el único camino (ver
# recordatorios_tab.js). No hace falta que corra seguido -- los turnos de
# mañana no cambian cada minuto.
DRAPP_SYNC_INTERVAL_SECONDS = 600
_ultimo_sync_drapp = None

# Mensajes de WhatsApp entrantes -- acá sí conviene un intervalo corto: a
# diferencia de los recordatorios (ventana de horas, no hay apuro), un
# paciente que mandó un WhatsApp está esperando una respuesta real, así que
# el borrador se genera lo antes posible para que quien aprueba lo vea rápido.
MENSAJES_WHATSAPP_CHECK_INTERVAL_SECONDS = 15
_ultimo_chequeo_mensajes_whatsapp = None

# Pedidos de factura -- mismo intervalo corto que los mensajes, misma razón
# (alguien está esperando el borrador para poder aprobarlo). Genera SOLO el
# borrador (monto/concepto/cliente) -- nunca emite, eso sigue siendo 100%
# manual vía /api/v1/facturas/<id>/aprobar, Director-only (ver facturas.py).
FACTURAS_CHECK_INTERVAL_SECONDS = 15
_ultimo_chequeo_facturas = None

# Mail entrante (consultorio + Abate) -- a diferencia de WhatsApp, un mail no
# tiene la misma urgencia implícita (nadie espera respuesta en minutos), así
# que alcanza con un intervalo más relajado tanto para traer mail nuevo de
# Gmail como para generarle el borrador.
MAIL_SYNC_INTERVAL_SECONDS = 120
_ultimo_sync_mail = None
MAIL_CHECK_INTERVAL_SECONDS = 60
_ultimo_chequeo_mail = None

# v0.2.1-rc6: respaldo estructural ADEMÁS del arreglo de la máquina de estados
# (ver tasks.py) -- ninguna tarea puede pasar por _process_classification()
# más de esta cantidad de veces, pase lo que pase. Es la segunda red de
# seguridad pedida explícitamente: si algún otro bug futuro lograra devolver
# una tarea a 'received'/'parsing' repetidamente, este límite la corta antes
# de que eso se traduzca en llamados ilimitados a un proveedor de IA.
MAX_EXTRACTION_ATTEMPTS = 2

# Identidad de ESTE proceso, generada una sola vez al importar el módulo.
# Usada por _claim_next_task() para que dos sidecars corriendo por error contra
# la misma base nunca puedan ejecutar la misma tarea a la vez (v0.2.1-rc1,
# punto 5 de la revisión). Modelo de amenaza real: una sola Mac, un solo
# Director -- esto cubre el caso de un sidecar huérfano que no se cerró bien
# tras un reinicio de Electron, no un sistema distribuido genérico.
WORKER_ID = uuid.uuid4().hex[:12]


def _clear_stale_locks():
    """Al arrancar, cualquier lock de una corrida anterior es por definición
    huérfano -- este proceso es, en el instante del arranque, la única
    instancia legítima corriendo. Se limpia antes de reconciliar nada más."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE tasks SET locked_by = NULL, locked_at = NULL WHERE state IN (?, ?, ?)",
        QUEUED_STATES,
    )
    conn.commit()


def recover_orphaned_tasks():
    _clear_stale_locks()
    for task in tasks.list_tasks():
        state = task["state"]
        if state == "executing":
            attempt = tasks.get_running_execution_attempt(task["task_id"])
            if attempt is None:
                # No debería pasar (execute_action siempre crea la fila antes
                # de todo), pero por las dudas no se asume nada.
                tasks.transition(
                    task["task_id"], "needs_review", "system",
                    detail={"motivo": "recuperación al reiniciar", "aviso": "No se encontró intento de ejecución asociado -- revisar manualmente."},
                )
                continue

            verdict = centro_mando_adapter.reconcile_execution_attempt(attempt)

            if verdict == "effect_confirmed":
                tasks.finish_execution_attempt(attempt["execution_id"], "effect_confirmed")
                tasks.transition(
                    task["task_id"], "completed", "system",
                    detail={
                        "motivo": "recuperación al reiniciar", "reconciliacion": "effect_confirmed",
                        "aviso": (
                            "El movimiento se había registrado correctamente antes de la "
                            "interrupción -- confirmado automáticamente contra el ledger de "
                            "operaciones de registrar_movimiento.py."
                        ),
                    },
                )
                sys.stderr.write(
                    f"[worker] tarea {task['task_id']} interrumpida pero YA ejecutada -> completed (reconciliado)\n"
                )
            else:
                tasks.finish_execution_attempt(
                    attempt["execution_id"], verdict,
                    error_message="Interrumpido por reinicio del proceso — no se reintenta automáticamente.",
                )
                aviso = (
                    "No se encontró evidencia de que el movimiento se haya registrado "
                    "(probablemente NO se ejecutó)."
                    if verdict == "effect_failed" else
                    "No se pudo verificar con certeza si el movimiento se registró -- "
                    "el ledger de operaciones no se pudo leer."
                )
                tasks.transition(
                    task["task_id"], "needs_review", "system",
                    detail={
                        "motivo": "recuperación al reiniciar", "reconciliacion": verdict,
                        "aviso": aviso + " Resolvé desde la Bandeja de tareas: confirmar ejecutada, "
                                 "confirmar no ejecutada y reintentar, cancelar, o mantener en revisión.",
                    },
                )
                sys.stderr.write(
                    f"[worker] tarea {task['task_id']} interrumpida a mitad de ejecución -> needs_review ({verdict})\n"
                )
        elif state in QUEUED_STATES:
            sys.stderr.write(f"[worker] tarea {task['task_id']} en '{state}' será reencolada\n")


def _claim_next_task():
    """Reclamo atómico vía UPDATE...WHERE (no SELECT-y-después-UPDATE, que
    dejaba una ventana de carrera entre leer y escribir). Si dos procesos
    compitieran por la misma tarea, SQLite serializa los UPDATE concurrentes
    (WAL + busy_timeout, ver db.py) -- solo uno ve rowcount==1."""
    conn = db.get_connection()
    now = datetime.datetime.utcnow().isoformat()
    for state in QUEUED_STATES:
        pending = tasks.list_tasks(state=state)
        for candidate in reversed(pending):  # list_tasks ordena DESC -> reversed = más viejo primero
            cur = conn.execute(
                "UPDATE tasks SET locked_by = ?, locked_at = ? WHERE task_id = ? AND state = ? "
                "AND (locked_by IS NULL OR locked_by = ?)",
                (WORKER_ID, now, candidate["task_id"], state, WORKER_ID),
            )
            conn.commit()
            if cur.rowcount == 1:
                return tasks.get_task_dict(candidate["task_id"])
    return None


def _find_prepared_action(task_id):
    events = tasks.get_task_events(task_id)
    for e in reversed(events):
        detail = e.get("detail_json")
        if isinstance(detail, str):
            try:
                detail = json.loads(detail)
            except json.JSONDecodeError:
                detail = {}
        if detail and "prepared_action" in detail:
            return detail["prepared_action"]
    return None


def _finish_classification(task_id, extracted, actor, attempt_count, provider_label=None, fell_back_from=None):
    """Cola común de la clasificación una vez que hay datos ESTRUCTURALMENTE
    válidos y con dominio soportado (cfo/abate) -- la usan tanto
    _process_classification() (extracción real, `actor='ai'`) como
    provide_missing_info() (Nicolás completó el dato a mano, `actor` es el
    user_id del Director, CERO llamados a un proveedor). No decide riesgo ni
    permisos -- eso sigue siendo 100% centro_mando_adapter.py."""
    domain = extracted.get("domain")
    intent = extracted.get("intent")
    risk = centro_mando_adapter.evaluate_risk(domain, intent, extracted)
    current = tasks.get_task_dict(task_id)
    new_revision = (current.get("task_revision") or 0) + 1

    detail = {"task_revision": new_revision, "extraction_attempts": attempt_count}
    if provider_label:
        detail["extraction_provider"] = provider_label
    if fell_back_from:
        detail["fell_back_from"] = fell_back_from

    tasks.transition(
        task_id, "classified", actor,
        detail=detail,
        domain=domain, intent=intent, extracted_json=extracted, risk_level=risk,
        task_revision=new_revision, extraction_attempts=attempt_count,
        # Trazabilidad de política (v0.2.1-rc1, punto 6): con qué versión/hash
        # exactos de policies/risk_policy.yaml se clasificó esta tarea.
        policy_version=centro_mando_adapter.POLICY_VERSION, policy_hash=centro_mando_adapter.POLICY_HASH,
    )

    try:
        prepared = centro_mando_adapter.prepare_action(domain, intent, extracted)
        action_hash = tasks.compute_action_hash(prepared)
    except Exception as e:
        # v0.2.1-rc6: `prepare_action` puede rechazar una combinación
        # dominio+intent que no soporta (ej. intent="other" para cfo -- ver
        # UnsupportedDomain en centro_mando_adapter.py). Sin este try/except,
        # la tarea quedaba parada en 'classified' sin avanzar ni quedar
        # visible como un problema -- misma filosofía que el resto del fix:
        # ninguna excepción se descarta sin una transición explícita.
        sys.stderr.write(f"[worker] ERROR preparando acción para {task_id}: {type(e).__name__}: {e}\n")
        tasks.transition(task_id, "needs_review", "system",
                          detail={"error": str(e)}, error_message=f"No se pudo preparar la acción: {e}")
        return

    if risk == "simple":
        tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                          detail={"prepared_action": prepared, "task_revision": new_revision})
    else:
        tasks.transition(task_id, "pending_approval", "system", action_version_hash=action_hash,
                          detail={"prepared_action": prepared, "task_revision": new_revision})


def _missing_required_fields(extracted):
    """Campos de logging (register_expense/register_income) que faltan --
    None si el intent no es de logging o si no falta nada. Reusa
    REQUIRED_FIELDS_CFO (misma política que ya usaba evaluate_risk() para
    decidir simple vs approval_required) -- acá se usa ANTES, para no dejar
    avanzar una tarea con datos incompletos a una revisión de riesgo que
    igual la mandaría a "pedile que apruebe algo a medio llenar"."""
    intent = extracted.get("intent")
    if intent not in centro_mando_adapter.LOGGING_INTENTS:
        return None
    missing = [f for f in centro_mando_adapter.REQUIRED_FIELDS_CFO if not extracted.get(f)]
    return missing or None


def _process_classification(task):
    """received/parsing -> classified -> (needs_information | pending_approval | ready).
    O, ante cualquier falla de clasificación: needs_information (dominio
    ambiguo o campos insuficientes -- sin más llamados automáticos) o
    needs_review (proveedores de IA no disponibles, o límite de reintentos
    agotado) -- nunca queda atascada en 'parsing' (v0.2.1-rc6, ver tasks.py)."""
    task_id = task["task_id"]
    attempt_count = (task.get("extraction_attempts") or 0) + 1

    try:
        if attempt_count > MAX_EXTRACTION_ATTEMPTS:
            # Respaldo estructural -- ver MAX_EXTRACTION_ATTEMPTS. CERO
            # llamados a un proveedor en esta rama a propósito.
            sys.stderr.write(
                f"[worker] tarea {task_id} superó MAX_EXTRACTION_ATTEMPTS "
                f"({MAX_EXTRACTION_ATTEMPTS}) -> needs_review sin más intentos.\n"
            )
            tasks.transition(
                task_id, "needs_review", "system",
                detail={"motivo": "límite de reintentos de clasificación alcanzado", "extraction_attempts": attempt_count},
                error_message=f"Se alcanzó el límite de {MAX_EXTRACTION_ATTEMPTS} intentos de clasificación sin éxito.",
                extraction_attempts=attempt_count,
            )
            return

        if task["state"] == "received":
            tasks.transition(task_id, "parsing", "system", extraction_attempts=attempt_count)

        extraction = ai_router.extract(task["raw_text"])
        outcome = extraction.get("outcome")

        if outcome == "auth_error":
            tasks.transition(
                task_id, "failed", "system",
                detail={"motivo": "proveedor de IA sin configurar correctamente", "error": extraction.get("error"),
                        "attempts": extraction.get("attempts")},
                error_message=f"Revisá la clave de API en Ajustes: {extraction.get('error')}",
                extraction_attempts=attempt_count,
            )
            return

        if outcome == "both_failed":
            tasks.transition(
                task_id, "needs_review", "system",
                detail={"motivo": "ningún proveedor de IA pudo responder", "error": extraction.get("error"),
                        "attempts": extraction.get("attempts")},
                error_message=f"Ningún proveedor de IA disponible en este momento: {extraction.get('error')}",
                extraction_attempts=attempt_count,
            )
            return

        # outcome == 'success' -- estructuralmente válido (ver
        # ai_router._validate_extraction_shape), pero el dominio puede
        # perfectamente ser "unknown" -- eso NO es un fallo de la IA, es una
        # ambigüedad real del pedido, y no amerita ningún llamado más.
        extracted = extraction["data"]
        domain = centro_mando_adapter.classify_request(extracted)
        if domain is None:
            tasks.transition(
                task_id, "needs_information", "system",
                detail={"pregunta": "No pude determinar si esto es de CFO o de Abate — ¿podés aclararlo?",
                        "extraction_provider": extraction.get("provider")},
                domain=extracted.get("domain"), intent=extracted.get("intent"), extracted_json=extracted,
                error_message="Dominio ambiguo o fuera de alcance (ni CFO ni Abate) — falta que aclares a qué corresponde.",
                extraction_attempts=attempt_count,
            )
            return

        faltantes = _missing_required_fields(extracted)
        if faltantes:
            tasks.transition(
                task_id, "needs_information", "system",
                detail={"pregunta": f"Faltan datos para registrar esto: {', '.join(faltantes)}.",
                        "extraction_provider": extraction.get("provider")},
                domain=domain, intent=extracted.get("intent"), extracted_json=extracted,
                error_message=f"Faltan campos: {', '.join(faltantes)}.",
                extraction_attempts=attempt_count,
            )
            return

        _finish_classification(
            task_id, extracted, "ai", attempt_count,
            provider_label=extraction.get("provider"), fell_back_from=extraction.get("fell_back_from"),
        )
    except Exception as e:
        # v0.2.1-rc6: esta excepción YA NO puede quedar sin una transición
        # válida -- 'needs_review' es alcanzable desde 'parsing' (ver
        # tasks.py). El try/except interno queda solo como último resorte
        # ante algo verdaderamente inesperado (ej. la propia base sin
        # conexión) -- y ni así se descarta en silencio: se deja constancia
        # en stderr, visible en los logs del sidecar.
        sys.stderr.write("[worker] ERROR clasificando: " + traceback.format_exc() + "\n")
        try:
            tasks.transition(task_id, "needs_review", "system",
                              detail={"error": str(e)}, error_message=str(e),
                              extraction_attempts=attempt_count)
        except Exception as inner_e:
            sys.stderr.write(
                f"[worker] ERROR -- no se pudo ni siquiera transicionar a needs_review para {task_id}: "
                f"{type(inner_e).__name__}: {inner_e}\n"
            )


def provide_missing_info(task_id: str, actor: str, updates: dict):
    """El Director completa a mano lo que falta en una tarea 'needs_information'
    (típicamente el dominio, cuando la IA lo dejó en "unknown"). CERO llamados
    a un proveedor de IA -- se combina lo que ya se había extraído con
    `updates` y se reevalúa con la misma lógica determinística de siempre. Si
    TODAVÍA falta algo, la tarea se queda en needs_information con un evento
    nuevo explicando qué sigue faltando (nunca en silencio)."""
    task = tasks.get_task_dict(task_id)
    if task is None:
        raise ValueError(f"Tarea {task_id} no existe.")
    if task["state"] != "needs_information":
        raise tasks.InvalidTransition(
            f"provide_missing_info solo aplica a tareas en 'needs_information' (esta está en '{task['state']}')."
        )
    # v0.2.1-rc7: validación explícita del dominio ANTES de tocar la tarea --
    # no alcanza con que classify_request() termine rechazando un valor
    # inesperado más abajo (eso ya pasaba, pero de forma implícita); acá se
    # rechaza de entrada cualquier cosa que no sea uno de los dominios que el
    # negocio soporta (cfo/abate -- ver policies/risk_policy.yaml), sin
    # escribir nada en la tarea ni en el historial.
    if "domain" in updates and updates["domain"] not in centro_mando_adapter.SUPPORTED_DOMAINS:
        raise ValueError(
            f"Dominio no permitido: {updates['domain']!r}. "
            f"Valores válidos: {sorted(centro_mando_adapter.SUPPORTED_DOMAINS)}."
        )

    extracted = task.get("extracted_json") or {}
    if isinstance(extracted, str):
        extracted = json.loads(extracted) if extracted else {}
    merged = {**extracted, **updates}

    domain = centro_mando_adapter.classify_request(merged)
    if domain is None:
        tasks.transition(
            task_id, "needs_information", actor,
            detail={"pregunta": "Sigue sin quedar claro si es de CFO o de Abate.", "updates": updates},
            domain=merged.get("domain"), extracted_json=merged,
            error_message="Dominio todavía ambiguo tras la aclaración -- probá con 'cfo' o 'abate' explícitamente.",
        )
        return tasks.get_task_dict(task_id)

    faltantes = _missing_required_fields(merged)
    if faltantes:
        tasks.transition(
            task_id, "needs_information", actor,
            detail={"pregunta": f"Todavía faltan: {', '.join(faltantes)}.", "updates": updates},
            domain=domain, extracted_json=merged,
            error_message=f"Todavía faltan campos: {', '.join(faltantes)}.",
        )
        return tasks.get_task_dict(task_id)

    _finish_classification(task_id, merged, actor, task.get("extraction_attempts") or 0)
    return tasks.get_task_dict(task_id)


def _process_execution(task):
    """ready -> executing -> completed | failed | needs_review."""
    task_id = task["task_id"]
    prepared = _find_prepared_action(task_id)
    if prepared is None:
        tasks.transition(task_id, "needs_review", "system",
                          detail={"error": "no se encontró la acción preparada en el historial"})
        return
    try:
        tasks.transition(task_id, "executing", "system")
        result = centro_mando_adapter.execute_action(task_id, prepared)
        centro_mando_adapter.record_result(task_id, "system", result)
    except Exception as e:
        sys.stderr.write("[worker] ERROR ejecutando: " + traceback.format_exc() + "\n")
        try:
            tasks.transition(task_id, "needs_review", "system", detail={"error": str(e)})
        except Exception:
            pass


def _procesar_recordatorios_si_corresponde():
    """Se llama en cada vuelta del loop pero es un no-op salvo que hayan
    pasado RECORDATORIOS_CHECK_INTERVAL_SECONDS desde el último chequeo --
    así el loop principal de tareas (que corre cada 1s) no se ve afectado."""
    global _ultimo_chequeo_recordatorios
    # Hora LOCAL, no UTC -- los turnos están cargados en hora argentina y la
    # ventana de envío se calcula contra este valor (auditoría 23/07).
    ahora = datetime.datetime.now()
    if _ultimo_chequeo_recordatorios is not None:
        transcurrido = (ahora - _ultimo_chequeo_recordatorios).total_seconds()
        if transcurrido < RECORDATORIOS_CHECK_INTERVAL_SECONDS:
            return
    _ultimo_chequeo_recordatorios = ahora

    for r in recordatorios.turnos_para_enviar_ahora(ahora):
        mensaje = recordatorios.mensaje_recordatorio(r)
        try:
            twilio_client.enviar_whatsapp(r["telefono"], mensaje)
            recordatorios.marcar_resultado(r["id"], "enviado")
        except (twilio_client.TwilioConfigError, twilio_client.TwilioSendError) as e:
            sys.stderr.write(f"[worker] ERROR enviando recordatorio {r['id']}: {e}\n")
            recordatorios.marcar_resultado(r["id"], "fallo_envio")


def _sincronizar_drapp_si_corresponde():
    """No-op si DRAPP_API_KEY no está configurado -- así el Director puede
    seguir usando el formulario manual sin que esto cambie nada hasta que
    él mismo cargue la clave en Ajustes."""
    global _ultimo_sync_drapp
    if not os.getenv("DRAPP_API_KEY"):
        return
    # Hora local también acá -- las fechas desde/hasta que se le piden a
    # DrApp son días calendario argentinos, no UTC (que cambia de día 3hs
    # antes y podría saltearse los turnos de la mañana siguiente).
    ahora = datetime.datetime.now()
    if _ultimo_sync_drapp is not None:
        transcurrido = (ahora - _ultimo_sync_drapp).total_seconds()
        if transcurrido < DRAPP_SYNC_INTERVAL_SECONDS:
            return
    _ultimo_sync_drapp = ahora

    desde = ahora.date().isoformat()
    hasta = (ahora + datetime.timedelta(days=2)).date().isoformat()
    try:
        eventos = drapp_client.list_turnos_medicina_general(desde, hasta)
    except (drapp_client.DrAppConfigError, drapp_client.DrAppAPIError) as e:
        sys.stderr.write(f"[worker] ERROR sincronizando con DrApp: {e}\n")
        return

    turnos = []
    for evento in eventos:
        consumer = evento.get("consumer") or {}
        consumer_id = consumer.get("id")
        nombre = f"{consumer.get('lastName', '')}, {consumer.get('firstName', '')}".strip(", ")
        telefono = None
        if consumer_id:
            try:
                telefono = drapp_client.get_telefono_paciente(consumer_id)
            except drapp_client.DrAppAPIError as e:
                sys.stderr.write(f"[worker] ERROR consultando teléfono de {consumer_id}: {e}\n")
        turnos.append({
            "drapp_event_id": evento.get("id"),
            "paciente_nombre": nombre or "(sin nombre en DrApp)",
            "telefono": telefono,
            "fecha_turno": evento.get("day"),
            "hora_turno": evento.get("time"),
            "cobertura": "",
            "practica": (evento.get("service") or {}).get("label", ""),
        })
    if turnos:
        # v0.2.6 -- creado_by tiene FK real contra users(user_id) -- "drapp-sync"
        # no es una persona real, así que esto rompía con IntegrityError apenas
        # corría (encontrado en vivo, se llevó el worker entero de fondo: los
        # loops de recordatorios/WhatsApp/mail/facturas/tareas comparten este
        # mismo hilo, ver run_forever). Mismo criterio que
        # /api/v1/recordatorios/importar y facturas.py para acciones
        # automáticas: se atribuyen al Director dueño de la cuenta.
        recordatorios.sincronizar_desde_drapp(turnos, sincronizado_by="nicolas")


def _procesar_mensajes_whatsapp_si_corresponde():
    """Cada mensaje 'recibido' pasa por la IA UNA vez por tick -- si falla
    (both_failed/auth_error), `generar_borrador` ya lo dejó en
    'error_clasificacion' con el texto original intacto, así que no se
    reintenta solo en el próximo tick (evitaría gastar llamadas de IA en
    bucle si la clave está mal configurada) -- queda visible en la bandeja
    para que una persona lo redacte a mano."""
    global _ultimo_chequeo_mensajes_whatsapp
    ahora = datetime.datetime.now()
    if _ultimo_chequeo_mensajes_whatsapp is not None:
        transcurrido = (ahora - _ultimo_chequeo_mensajes_whatsapp).total_seconds()
        if transcurrido < MENSAJES_WHATSAPP_CHECK_INTERVAL_SECONDS:
            return
    _ultimo_chequeo_mensajes_whatsapp = ahora

    for m in mensajes_whatsapp.mensajes_pendientes_de_borrador():
        try:
            mensajes_whatsapp.generar_borrador(m["id"])
        except mensajes_whatsapp.MensajeWhatsappError as e:
            sys.stderr.write(f"[worker] ERROR generando borrador para mensaje {m['id']}: {e}\n")


def _sincronizar_mail_si_corresponde():
    """No-op por casilla si sus credenciales de Gmail no están configuradas
    (ver gmail_client._config) -- así se puede activar una casilla sin
    esperar tener la otra lista."""
    global _ultimo_sync_mail
    ahora = datetime.datetime.now()
    if _ultimo_sync_mail is not None:
        transcurrido = (ahora - _ultimo_sync_mail).total_seconds()
        if transcurrido < MAIL_SYNC_INTERVAL_SECONDS:
            return
    _ultimo_sync_mail = ahora

    for casilla in mail_entrante.CASILLAS_VALIDAS:
        try:
            mail_entrante.sincronizar_casilla(casilla)
        except gmail_client.GmailConfigError:
            continue  # casilla todavía sin credenciales cargadas, no es un error
        except gmail_client.GmailSendError as e:
            sys.stderr.write(f"[worker] ERROR sincronizando mail de '{casilla}': {e}\n")


def _procesar_mail_si_corresponde():
    """Mismo criterio que _procesar_mensajes_whatsapp_si_corresponde: un
    único intento de IA por tick, nunca reintenta solo si falla (queda en
    'error_clasificacion')."""
    global _ultimo_chequeo_mail
    ahora = datetime.datetime.now()
    if _ultimo_chequeo_mail is not None:
        transcurrido = (ahora - _ultimo_chequeo_mail).total_seconds()
        if transcurrido < MAIL_CHECK_INTERVAL_SECONDS:
            return
    _ultimo_chequeo_mail = ahora

    for m in mail_entrante.mails_pendientes_de_borrador():
        try:
            mail_entrante.generar_borrador(m["id"])
        except mail_entrante.MailEntranteError as e:
            sys.stderr.write(f"[worker] ERROR generando borrador de mail {m['id']}: {e}\n")


def _procesar_facturas_si_corresponde():
    """Mismo criterio que _procesar_mensajes_whatsapp_si_corresponde: un
    único intento de IA por tick, nunca reintenta solo si falla (queda en
    'error_extraccion', visible para armar el borrador a mano). Esta función
    SOLO arma el borrador -- jamás llama a facturas.aprobar_y_emitir."""
    global _ultimo_chequeo_facturas
    ahora = datetime.datetime.now()
    if _ultimo_chequeo_facturas is not None:
        transcurrido = (ahora - _ultimo_chequeo_facturas).total_seconds()
        if transcurrido < FACTURAS_CHECK_INTERVAL_SECONDS:
            return
    _ultimo_chequeo_facturas = ahora

    for f in facturas.pedidos_pendientes_de_borrador():
        try:
            facturas.generar_borrador(f["id"])
        except facturas.FacturaError as e:
            sys.stderr.write(f"[worker] ERROR generando borrador de factura {f['id']}: {e}\n")


def run_forever():
    recover_orphaned_tasks()
    sys.stderr.write("[worker] loop arrancado\n")
    while True:
        # v0.2.6 -- hallazgo real: un IntegrityError sin atrapar en
        # _sincronizar_drapp_si_corresponde tiró abajo este hilo ENTERO --
        # como todas las revisiones periódicas (recordatorios, WhatsApp,
        # facturas, mail) y el procesamiento de tareas comparten el mismo
        # loop, un bug en una sola de ellas dejaba TODO el trabajo de fondo
        # muerto en silencio (el servidor HTTP seguía respondiendo, pero
        # ninguna tarea nueva se procesaba nunca más) hasta reiniciar la app
        # a mano. Cada función ya maneja sus propios errores esperables
        # puntualmente -- este try/except de acá es la red de seguridad
        # para lo que ninguna previó, para que un bug futuro cueste como
        # mucho un tick salteado, no todo el sistema.
        try:
            _procesar_recordatorios_si_corresponde()
            _sincronizar_drapp_si_corresponde()
            _procesar_mensajes_whatsapp_si_corresponde()
            _procesar_facturas_si_corresponde()
            _sincronizar_mail_si_corresponde()
            _procesar_mail_si_corresponde()
            task = _claim_next_task()
            if task is None:
                time.sleep(POLL_INTERVAL_SECONDS)
                continue
            if task["state"] in ("received", "parsing"):
                _process_classification(task)
            elif task["state"] == "ready":
                _process_execution(task)
        except Exception:
            sys.stderr.write("[worker] ERROR no esperado en el loop -- se sigue en el próximo tick:\n" + traceback.format_exc() + "\n")
            time.sleep(POLL_INTERVAL_SECONDS)
