"""
Cliente de la API pública de DrApp/Cormos (v0.2.6) -- lee y escribe la agenda
real del consultorio: turnos, disponibilidad, pacientes (incluye teléfono).
Reemplaza el mecanismo manual de recordatorios (formulario del Director) por
una fuente automática, y es la base para que el bot de WhatsApp pueda ofrecer
y confirmar turnos contra disponibilidad real -- ver plan v0.2.6, Fase A.

Spec verificado en vivo el 21/07/2026 contra `https://api.drapp.com.ar/public/v1/openapi.yaml`
(no solo leído -- se probaron requests reales contra la API, ver plan). Igual
mecanismo que `sheets_client.py`/`twilio_client.py`: usa `urllib.request` de la
stdlib, sin agregar dependencias nuevas para esto.

Nunca asume que "consultar disponibilidad" y "crear turno" son atómicos -- un
slot puede ocuparse entre una llamada y la otra (409 `conflict`), así que
`crear_turno` propaga ese caso como `DrAppConflictError` para que quien llama
(el motor de conversación del bot, más adelante) vuelva a ofrecer en vez de
asumir éxito o fallar en silencio.
"""
import json
import os
import urllib.error
import urllib.parse
import urllib.request
import uuid

BASE_URL = "https://api.drapp.com.ar/public/v1"
REQUEST_TIMEOUT_SECONDS = 10


class DrAppConfigError(Exception):
    pass


class DrAppAPIError(Exception):
    """Error devuelto por la API (formato {error, message, status, request_id})."""

    def __init__(self, error_code, message, status):
        self.error_code = error_code
        self.status = status
        super().__init__(f"DrApp rechazó la request ({status} {error_code}): {message}")


class DrAppConflictError(DrAppAPIError):
    """El slot se ocupó entre que se consultó disponibilidad y se intentó crear
    el turno -- nunca tratar esto como un error genérico, hay que volver a
    ofrecer otro horario, no fallar en silencio ni asumir que el turno quedó."""
    pass


def _config():
    api_key = os.getenv("DRAPP_API_KEY")
    team_id = os.getenv("DRAPP_TEAM_ID")
    if not api_key or not team_id:
        raise DrAppConfigError(
            "Falta configurar DrApp (API key y team ID) en la pantalla de Ajustes de la app."
        )
    return api_key, team_id


def _strip_prefix(value: str, prefix: str) -> str:
    return value[len(prefix):] if value.startswith(prefix) else value


def _request(method: str, path: str, params: dict = None, body: dict = None, idempotency_key: str = None):
    api_key, _ = _config()
    url = f"{BASE_URL}{path}"
    if params:
        clean_params = {k: v for k, v in params.items() if v is not None}
        if clean_params:
            url += "?" + urllib.parse.urlencode(clean_params)

    data = json.dumps(body).encode("utf-8") if body is not None else None
    request = urllib.request.Request(url, data=data, method=method)
    request.add_header("Authorization", f"Bearer {api_key}")
    if data is not None:
        request.add_header("Content-Type", "application/json")
    if idempotency_key and method in ("POST", "PATCH", "PUT"):
        request.add_header("Idempotency-Key", idempotency_key)

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            raw = response.read()
            return json.loads(raw) if raw else None
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", errors="replace")
        try:
            payload = json.loads(raw)
            error_code = payload.get("error", "unknown_error")
            message = payload.get("message", raw)
        except json.JSONDecodeError:
            error_code = "unknown_error"
            message = raw
        if e.code == 409:
            raise DrAppConflictError(error_code, message, e.code)
        raise DrAppAPIError(error_code, message, e.code)
    except urllib.error.URLError as e:
        raise DrAppAPIError("connection_error", str(e.reason), 0)


# ---- Disponibilidad y turnos --------------------------------------------

def consultar_disponibilidad(resource_id: str, service_key: str, desde: str, hasta: str):
    """`resource_id` como "resources/dr-garcia", `desde`/`hasta` en YYYY-MM-DD.
    Devuelve la lista de {date, resource, slots: [{time, available}]} tal cual
    la entrega DrApp -- no se filtra ni se interpreta acá."""
    _, team_id = _config()
    return _request(
        "GET", f"/teams/{team_id}/availability",
        params={"resource": resource_id, "service": service_key, "from": desde, "to": hasta},
    )


def crear_turno(resource_id: str, service_key: str, consumer_id: str, day: str, time: str, idempotency_key: str = None):
    """Puede levantar DrAppConflictError si el slot se ocupó justo antes --
    quien llama tiene que manejar ese caso ofreciendo otro horario, nunca
    asumir que el turno se creó.

    `idempotency_key`: si quien llama va a reintentar esta MISMA operación
    ante un timeout (ej. el motor de conversación del bot reintentando tras
    no recibir respuesta), tiene que pasar la misma key en cada intento --
    así DrApp devuelve el turno ya creado en vez de agendarlo dos veces. Si
    no se pasa, se genera una nueva por llamada (comportamiento de "solo una
    vez", sin protección ante reintentos externos)."""
    _, team_id = _config()
    return _request(
        "POST", f"/teams/{team_id}/events",
        body={
            "resource": {"id": resource_id},
            "service": {"key": service_key},
            "consumer": {"id": consumer_id},
            "day": day,
            "time": time,
        },
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


def cancelar_turno(event_id: str, idempotency_key: str = None):
    _, team_id = _config()
    event_id = _strip_prefix(event_id, "events/")
    return _request(
        "DELETE", f"/teams/{team_id}/events/{event_id}",
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


def reprogramar_turno(event_id: str, day: str, time: str, idempotency_key: str = None):
    _, team_id = _config()
    event_id = _strip_prefix(event_id, "events/")
    return _request(
        "POST", f"/teams/{team_id}/events/{event_id}", body={"day": day, "time": time},
        idempotency_key=idempotency_key or uuid.uuid4().hex,
    )


def list_turnos_medicina_general(desde: str, hasta: str):
    """Filtra en nuestro propio código -- la API de DrApp no tiene un filtro
    de "tipo de práctica" en /events, así que se excluye por `service.label`
    (mismo enfoque por palabras clave que ya usa el bot de n8n).

    v0.2.6 -- confirmado en vivo contra la cuenta real: `/events` con
    `status=booked` también devuelve entradas `type: "lock"` (bloqueos de
    agenda / feriados, sin paciente ni service real, `duration` de un día
    entero) -- no son turnos, y sin este filtro entraban como si lo fueran
    (`consumer` vacío, terminaban como "(sin nombre en DrApp)" en la
    bandeja de Marianela). `type` no está documentado en el schema oficial
    de Event, así que solo se excluye cuando está presente y NO es
    "appointment" -- si algún día falta el campo, no se pierde el turno por
    las dudas."""
    _, team_id = _config()
    eventos = _request(
        "GET", f"/teams/{team_id}/events",
        params={"from": desde, "to": hasta, "status": "booked", "expand": "consumer"},
    ) or []
    return [
        e for e in eventos
        if e.get("type") in (None, "appointment")
        and "psiquiatr" not in (e.get("service", {}).get("label", "") or "").lower()
    ]


# ---- Pacientes -------------------------------------------------------------

def _variantes_telefono_ar(telefono: str) -> list:
    """Un celular argentino en formato E.164 lleva un "9" después del "54"
    (+549...) -- así lo manda WhatsApp siempre. Pero DrApp puede tenerlo
    cargado sin ese "9" (+54...) -- confirmado en vivo (20/08): un paciente
    real no aparecía buscando por el número tal cual llegaba de WhatsApp,
    y sí estaba, guardado sin el "9". Devuelve el teléfono tal cual primero,
    y la variante con el "9" agregado o sacado como segundo intento --
    nunca asume cuál de las dos formas es la correcta."""
    variantes = [telefono]
    if telefono.startswith("+549"):
        variantes.append("+54" + telefono[4:])
    elif telefono.startswith("+54"):
        variantes.append("+549" + telefono[3:])
    return variantes


def buscar_paciente_por_telefono(telefono: str):
    """Devuelve el primer paciente que coincide, o None si no hay ninguno --
    nunca inventa ni asume una coincidencia parcial. Prueba la variante con/
    sin el "9" de celular argentino (ver _variantes_telefono_ar) antes de
    rendirse -- una sola llamada extra, solo si la primera no encontró nada.

    v0.2.6 -- este endpoint devuelve un array DIRECTO (`type: array` en el
    spec real, confirmado con una request real contra el sandbox), no
    `{"data": [...]}` como se había asumido antes sin poder probarlo contra
    la API de verdad -- la versión vieja explotaba (AttributeError) apenas
    hubiera un match real, y el test que lo cubría mockeaba la forma
    incorrecta, así que nunca lo detectó."""
    _, team_id = _config()
    for variante in _variantes_telefono_ar(telefono):
        pacientes = _request("GET", f"/teams/{team_id}/consumers", params={"phone": variante}) or []
        if pacientes:
            return pacientes[0]
    return None


def buscar_pacientes_por_texto(query: str) -> list:
    """Búsqueda fuzzy por nombre, apellido o DNI (`q` en el spec real -- un
    solo parámetro busca los tres). Devuelve TODOS los que matchean, sin
    filtrar a uno solo -- quien llama decide qué hacer si hay 0 o más de 1
    (nunca asumir cuál es el correcto). v0.2.6, Fase C: fallback cuando el
    teléfono no identificó a nadie."""
    _, team_id = _config()
    query = (query or "").strip()
    if not query:
        return []
    return _request("GET", f"/teams/{team_id}/consumers", params={"q": query}) or []


def get_telefono_paciente(consumer_id: str):
    """Devuelve el primer teléfono cargado, o None -- nunca inventa un número."""
    _, team_id = _config()
    consumer_id = _strip_prefix(consumer_id, "consumers/")
    paciente = _request("GET", f"/teams/{team_id}/consumers/{consumer_id}")
    telefonos = (paciente or {}).get("phones", [])
    return telefonos[0]["value"] if telefonos else None


def listar_turnos_de_paciente(consumer_id: str):
    """Turnos futuros de un paciente (todos los estados, todas las
    especialidades) -- filtrar por 'status'/'type'/especialidad es
    responsabilidad de quien llama, igual que list_turnos_medicina_general.
    v0.2.6, Fase C (cancelación por WhatsApp).

    v0.2.7 (20/08) -- hallazgo real: la forma real es
    {"next": [...], "past": [...]}, no un array directo como se había
    asumido sin poder probarlo contra la API real -- iterar directo sobre
    la respuesta terminaba iterando las CLAVES del dict ("next"/"past",
    strings) en vez de los turnos, y explotaba (AttributeError) apenas un
    paciente real tenía algo cargado. Solo interesa "next" acá -- no se
    puede cancelar un turno que ya pasó."""
    _, team_id = _config()
    consumer_id = _strip_prefix(consumer_id, "consumers/")
    resultado = _request("GET", f"/teams/{team_id}/consumers/{consumer_id}/events") or {}
    return resultado.get("next", [])
