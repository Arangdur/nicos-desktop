"""
Router de IA para la vista Director de NicOS Desktop: despacha cada consulta a
Claude o a OpenAI según lo que pida el front (parámetro `brain`), usando el
mismo patrón que ya probó `trading_bot/core/ai_copilot.py` (cliente lazy,
system prompt fijo, nunca inventar datos, contexto real como JSON).

El system prompt está calcado de las reglas ya vigentes y probadas en
`Centro de Mando/CLAUDE.md` y `jarvis-trabajo/CLAUDE.md` — no se reinventan acá,
se les da como instrucción fija al modelo para que el Director de la app
se comporte igual que el agente de Claude Code que ya usa Nicolás.
"""
import json
import os

_claude_client = None
_claude_error = None
_openai_client = None
_openai_error = None

PROVIDER_MATRIX_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "provider_matrix.json")

EXTRACTION_SYSTEM_PROMPT = """Convertís un pedido en texto libre (de Nicolás o su secretaria \
Marianela) en datos estructurados. NUNCA decidís si algo requiere aprobación ni ejecutás nada \
— eso lo hace código determinístico después de tu extracción.

Regla de dominio (de Centro de Mando/CLAUDE.md, no la reinventes): si es claramente institucional \
(Fundación Abate, consultorio compartido, personal de Abate) el dominio es "abate"; si es personal \
de Nicolás, el dominio es "cfo"; si no está claro o es de otro tipo (clínico, trading, etc.), \
el dominio es "unknown" — NUNCA inventes un dominio si no está claro en el texto.

Regla de intención: si el texto describe algo que YA PASÓ (un gasto que ya se hizo, una plata que \
ya entró), la intención es "register_expense" o "register_income". Si el texto pide INICIAR algo \
nuevo (pagarle a alguien, transferir, autorizar), la intención es "new_financial_action". Si no \
se puede determinar, "other".

Nunca inventes un monto, fecha o concepto que no esté en el texto — dejá esos campos null si faltan."""

EXTRACTION_JSON_SCHEMA = {
    "name": "extracted_task",
    "schema": {
        "type": "object",
        "properties": {
            "domain": {"type": "string", "enum": ["cfo", "abate", "unknown"]},
            "intent": {
                "type": "string",
                "enum": ["register_expense", "register_income", "new_financial_action", "other"],
            },
            "amount": {"type": ["number", "null"]},
            "date": {"type": ["string", "null"], "description": "Formato DD/MM/YYYY si el texto trae fecha, si no null (nunca inventar hoy)"},
            "concept": {"type": ["string", "null"]},
            "evidence": {"type": ["string", "null"], "description": "Cualquier detalle adicional relevante del texto original"},
        },
        "required": ["domain", "intent", "amount", "date", "concept", "evidence"],
        "additionalProperties": False,
    },
    "strict": True,
}


def get_provider_matrix() -> dict:
    try:
        with open(PROVIDER_MATRIX_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {"version": 0, "rules": {}, "fallback_on_primary_failure": True}


def get_provider_for(task_type: str, default: str = "claude") -> str:
    matrix = get_provider_matrix()
    return matrix.get("rules", {}).get(task_type, default)


DIRECTOR_SYSTEM_PROMPT = """Sos el Director de NicOS, el asistente de coordinación personal \
de Nicolás Buso (médico, Director Técnico de la Fundación Abate, dueño de un trading bot). \
Hablás español rioplatense, directo y sin vueltas.

Coordinás: el consultorio (Agente Médico Integral), las finanzas personales (CFO), la Fundación \
Abate, y el trading bot. Usás SOLO los datos reales que te paso en el contexto (JSON) — nunca \
inventes cifras, nombres ni estados que no estén ahí.

Regla de oro (SIMPLE vs. PENDIENTE DE TU OK — no negociable):
- SIMPLE (podés simplemente responder/resolver): resumir, cruzar información, redactar borradores \
sin enviar, ordenar información.
- PENDIENTE DE TU OK (avisá y esperá confirmación explícita, nunca lo resuelvas solo): cualquier \
cosa con plata, pacientes, compromisos de la Fundación, o que afecte más de un frente a la vez.

Regla de privacidad clínica (absoluta, sin excepción): nunca menciones nombre, DNI o diagnóstico \
de un paciente específico, incluso si aparece en el contexto que te pasan por error. Si el contexto \
trae datos identificatorios de un paciente, respondé solo con agregados (conteos, sí/no) y avisá \
que hay datos sensibles que no vas a repetir.

Si el contexto no alcanza para responder algo con precisión, decilo en vez de inventar.
Sé breve: 3-6 frases salvo que pidan un análisis largo.
"""


def _get_claude_client():
    global _claude_client, _claude_error
    if _claude_client is not None or _claude_error is not None:
        return _claude_client
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not api_key:
        _claude_error = "Falta configurar la API key de Anthropic en Ajustes."
        return None
    try:
        import anthropic
        _claude_client = anthropic.Anthropic(api_key=api_key)
        return _claude_client
    except ImportError:
        _claude_error = "Falta instalar la librería anthropic en el sidecar."
        return None


def _get_openai_client():
    global _openai_client, _openai_error
    if _openai_client is not None or _openai_error is not None:
        return _openai_client
    api_key = os.getenv("OPENAI_API_KEY")
    if not api_key:
        _openai_error = "Falta configurar la API key de OpenAI en Ajustes."
        return None
    try:
        import openai
        _openai_client = openai.OpenAI(api_key=api_key)
        return _openai_client
    except ImportError:
        _openai_error = "Falta instalar la librería openai en el sidecar."
        return None


def _build_context_block(context: dict) -> str:
    return "CONTEXTO ACTUAL DEL ECOSISTEMA (JSON):\n" + json.dumps(
        context, ensure_ascii=False, indent=2, default=str
    )


def _ask_claude(question, context, history):
    client = _get_claude_client()
    if client is None:
        return {"ok": False, "reply": _claude_error, "brain": "claude"}

    messages = []
    for turn in (history or [])[-6:]:
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({
        "role": "user",
        "content": f"{_build_context_block(context)}\n\nPREGUNTA:\n{question}",
    })

    try:
        resp = client.messages.create(
            model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
            max_tokens=800,
            system=DIRECTOR_SYSTEM_PROMPT,
            messages=messages,
        )
        text = "".join(block.text for block in resp.content if block.type == "text")
        return {"ok": True, "reply": text.strip(), "brain": "claude"}
    except Exception as e:
        return {"ok": False, "reply": f"Error consultando a Claude: {e}", "brain": "claude"}


def _ask_openai(question, context, history):
    client = _get_openai_client()
    if client is None:
        return {"ok": False, "reply": _openai_error, "brain": "openai"}

    messages = [{"role": "system", "content": DIRECTOR_SYSTEM_PROMPT}]
    for turn in (history or [])[-6:]:
        role = "user" if turn.get("role") == "user" else "assistant"
        messages.append({"role": role, "content": turn.get("content", "")})
    messages.append({
        "role": "user",
        "content": f"{_build_context_block(context)}\n\nPREGUNTA:\n{question}",
    })

    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            max_tokens=800,
            messages=messages,
        )
        text = resp.choices[0].message.content
        return {"ok": True, "reply": (text or "").strip(), "brain": "openai"}
    except Exception as e:
        return {"ok": False, "reply": f"Error consultando a OpenAI: {e}", "brain": "openai"}


def ask_director(question: str, context: dict, history: list = None, brain: str = "claude") -> dict:
    """brain: 'claude' | 'openai'. Cualquier otro valor cae a 'claude' por defecto."""
    if brain == "openai":
        return _ask_openai(question, context, history)
    return _ask_claude(question, context, history)


# v0.2.1-rc6 — resiliencia de extracción. Antes de esto, `extract()` hacía a
# lo sumo un fallback ad-hoc (openai -> claude) solo si `ok` era False, sin
# distinguir POR QUÉ falló ni validar la FORMA de una respuesta que sí vino
# marcada como exitosa. Eso permitía dos huecos reales, encontrados en el
# smoke test del 18/7/2026:
#   1. Una respuesta con `domain: "unknown"` (un valor legítimo del propio
#      schema) se trataba como éxito -- el llamador (`worker.py`) intentaba
#      una transición de estado que no existía, y la tarea quedaba atascada
#      reintentando la extracción real cada 1s, para siempre.
#   2. Nada bloqueaba una cadena de reintentos ilimitada si la respuesta
#      llegaba estructuralmente rota (JSON inválido, faltaban claves).
# Ahora `extract()` nunca hace más de 2 llamadas HTTP reales en total, siempre
# devuelve un `outcome` explícito, y clasifica el tipo de falla para decidir
# si vale la pena un segundo intento con el proveedor alternativo.

_AUTH_ERROR_TYPES = ("AuthenticationError", "PermissionDeniedError")

_REQUIRED_EXTRACTION_KEYS = set(EXTRACTION_JSON_SCHEMA["schema"]["required"])
_VALID_DOMAINS = set(EXTRACTION_JSON_SCHEMA["schema"]["properties"]["domain"]["enum"])
_VALID_INTENTS = set(EXTRACTION_JSON_SCHEMA["schema"]["properties"]["intent"]["enum"])


def _classify_exception(exc) -> str:
    """'auth' | 'transient' — Anthropic y OpenAI exponen nombres de excepción
    equivalentes (AuthenticationError, RateLimitError, APIConnectionError,
    etc.), así que alcanza con el nombre de la clase, sin importar de qué
    librería vino. 'auth': la key/config de ESE proveedor está mal — no tiene
    sentido reintentar CON EL MISMO proveedor, pero tampoco se enmascara
    probando el alternativo (ver política en `extract()`). Cualquier otra
    excepción (cuota, rate limit, timeout, 5xx del lado del proveedor, o algo
    no previsto) se trata como 'transient' — vale la pena una sola vez con el
    alternativo."""
    if type(exc).__name__ in _AUTH_ERROR_TYPES:
        return "auth"
    return "transient"


def _validate_extraction_shape(data):
    """None si `data` tiene la forma que pedimos (dict, todas las claves
    requeridas presentes, domain/intent dentro del enum, amount numérico o
    null) — si no, un string describiendo qué está mal. Deliberadamente NO
    evalúa si el dominio está soportado por el negocio (cfo/abate) — eso es
    ambigüedad de dominio, no una respuesta malformada, y lo decide después
    `centro_mando_adapter.classify_request()`. Acá solo se valida que lo que
    volvió el proveedor tenga la forma que el schema pidió."""
    if not isinstance(data, dict):
        return f"la respuesta no es un objeto JSON (vino {type(data).__name__})"
    missing = _REQUIRED_EXTRACTION_KEYS - set(data.keys())
    if missing:
        return f"faltan campos requeridos: {', '.join(sorted(missing))}"
    if data.get("domain") not in _VALID_DOMAINS:
        return f"'domain' fuera del rango esperado: {data.get('domain')!r}"
    if data.get("intent") not in _VALID_INTENTS:
        return f"'intent' fuera del rango esperado: {data.get('intent')!r}"
    amount = data.get("amount")
    if amount is not None and not isinstance(amount, (int, float)):
        return f"'amount' no es numérico: {amount!r}"
    return None


def _try_extract(provider_name: str, raw_text: str) -> dict:
    """UN único intento con UN proveedor — nunca reintenta internamente (eso
    lo orquesta `extract()`, que es quien controla el presupuesto total).
    status: 'success' | 'malformed' | 'auth_error' | 'transient_error'."""
    if provider_name == "openai":
        client = _get_openai_client()
        if client is None:
            return {"status": "auth_error", "provider": "openai", "error": _openai_error}
        try:
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5"),
                messages=[
                    {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                    {"role": "user", "content": raw_text},
                ],
                response_format={"type": "json_schema", "json_schema": EXTRACTION_JSON_SCHEMA},
            )
            raw_content = resp.choices[0].message.content
            try:
                data = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError) as e:
                return {"status": "malformed", "provider": "openai", "error": f"JSON inválido: {e}"}
        except Exception as e:
            status = "auth_error" if _classify_exception(e) == "auth" else "transient_error"
            return {"status": status, "provider": "openai", "error": f"{type(e).__name__}: {e}"}
    else:
        client = _get_claude_client()
        if client is None:
            return {"status": "auth_error", "provider": "claude", "error": _claude_error}
        try:
            resp = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
                max_tokens=500,
                system=EXTRACTION_SYSTEM_PROMPT,
                tools=[{
                    "name": "extracted_task",
                    "description": "Datos estructurados extraídos del pedido en texto libre.",
                    "input_schema": EXTRACTION_JSON_SCHEMA["schema"],
                }],
                tool_choice={"type": "tool", "name": "extracted_task"},
                messages=[{"role": "user", "content": raw_text}],
            )
            tool_block = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_block is None:
                return {"status": "malformed", "provider": "claude", "error": "la respuesta no incluyó el tool_use esperado"}
            data = tool_block.input
        except Exception as e:
            status = "auth_error" if _classify_exception(e) == "auth" else "transient_error"
            return {"status": status, "provider": "claude", "error": f"{type(e).__name__}: {e}"}

    shape_error = _validate_extraction_shape(data)
    if shape_error:
        return {"status": "malformed", "provider": provider_name, "error": shape_error}
    return {"status": "success", "provider": provider_name, "data": data}


def extract(raw_text: str, provider: str = None) -> dict:
    """Convierte texto libre en {domain, intent, amount, date, concept, evidence}.
    El proveedor primario se elige por la matriz versionada (provider_matrix.json)
    salvo que se pase explícitamente. Nunca decide riesgo ni si un dominio está
    soportado por el negocio — eso es 100% de `centro_mando_adapter.py`.

    Presupuesto DURO: nunca más de 2 llamadas HTTP reales por invocación, sin
    importar qué combinación de fallas ocurra.

    outcome devuelto:
      - 'success': respuesta con forma válida (schema-conformant) -- 'data',
        'provider' (y 'fell_back_from' si hizo falta el alternativo). El
        `domain` puede perfectamente ser "unknown" -- sigue siendo 'success'
        acá, la ambigüedad de negocio la resuelve el llamador.
      - 'auth_error': key/config inválida -- 'error', 'provider'. Nunca
        intenta el alternativo (evita enmascarar un proveedor mal configurado).
      - 'both_failed': se agotó el presupuesto sin una respuesta válida --
        'error' (combina el detalle de ambos intentos), 'attempts'.

    Política:
      - error transitorio o de cuota en el primario -> UN intento con el
        alternativo.
      - respuesta malformada/fuera de esquema del primario -> UN intento con
        el alternativo (mismo presupuesto -- es el "reintento controlado").
      - autenticación/configuración inválida -> error visible, sin intentar
        el alternativo.
    """
    provider = provider or get_provider_for("extract_task", default="openai")
    alternate = "claude" if provider == "openai" else "openai"

    attempts = []
    first = _try_extract(provider, raw_text)
    attempts.append(first)

    if first["status"] == "success":
        return {"outcome": "success", "data": first["data"], "provider": provider, "attempts": attempts}

    if first["status"] == "auth_error":
        return {"outcome": "auth_error", "provider": provider, "error": first["error"], "attempts": attempts}

    if not get_provider_matrix().get("fallback_on_primary_failure", True):
        return {"outcome": "both_failed", "error": first["error"], "attempts": attempts}

    second = _try_extract(alternate, raw_text)
    attempts.append(second)

    if second["status"] == "success":
        return {
            "outcome": "success", "data": second["data"], "provider": alternate,
            "fell_back_from": provider, "attempts": attempts,
        }

    combined_error = f"{provider}: {first['error']} | {alternate}: {second['error']}"
    return {"outcome": "both_failed", "error": combined_error, "attempts": attempts}


# v0.2.5 -- clasificación + borrador de respuesta para mensajes de WhatsApp
# ENTRANTES (primera vez que NicOS recibe algo, no solo manda). Mismo patrón
# de resiliencia que `extract()` -- presupuesto duro de 2 llamadas, nunca
# enmascara un proveedor mal configurado probando el otro. La diferencia de
# fondo con `extract()`: acá el texto lo escribe un PACIENTE, no Nicolás ni
# Marianela, así que el prompt es mucho más restrictivo sobre qué puede
# afirmar el borrador -- nunca inventa un horario disponible (no hay lookup
# real de disponibilidad en esta versión, DrApp todavía no tiene la API
# habilitada para el equipo) y nunca contesta nada clínico -- solo redacta,
# nunca envía (eso lo decide una persona en la Bandeja de WhatsApp).

MENSAJE_WHATSAPP_SYSTEM_PROMPT = """Sos el asistente que arma BORRADORES de respuesta para los \
mensajes de WhatsApp que le escriben pacientes al consultorio del Dr. Nicolás Buso. Nunca mandás \
nada vos -- una persona del consultorio revisa y aprueba cada borrador antes de que salga.

Clasificá el mensaje en una de estas categorías:
- turno_nuevo: pide sacar un turno.
- cancelacion: pide cancelar un turno que ya tiene.
- reprogramacion: pide cambiar la fecha/hora de un turno.
- consulta_general: pregunta administrativa (horarios de atención, dirección, obra social, precio).
- receta: pide una receta, renovación de medicación, o cualquier cosa clínica (síntomas, dudas \
sobre un tratamiento, interpretación de un estudio).
- ambiguo: no se entiende qué pide, o pide varias cosas a la vez.

`requiere_profesional`: true SIEMPRE que la clasificación sea "receta", o si el texto menciona \
síntomas, medicación, diagnóstico o cualquier cosa clínica aunque la clasificación principal sea \
otra. false para todo lo puramente administrativo (turnos, cancelaciones, consultas generales).

`urgente`: true si el texto sugiere una situación que no puede esperar la respuesta habitual \
(dolor agudo, crisis, palabras como "urgente" o "emergencia"). Esto NO dispara ninguna acción \
sola -- solo hace que la persona que revisa lo vea primero.

Reglas del borrador (`borrador_respuesta`), no negociables:
1. NUNCA inventes ni ofrezcas un horario específico disponible -- no tenés acceso a la agenda real. \
Si es turno_nuevo/reprogramacion, el borrador dice que alguien del consultorio va a confirmar el \
horario, nunca propone uno.
2. NUNCA respondas nada clínico (no interpretes síntomas, no confirmes ni sugieras medicación). Si \
`requiere_profesional` es true, el borrador solo avisa que el Dr. Buso va a revisar el pedido \
personalmente -- no intenta resolver el contenido médico.
3. Tono cordial, breve (2-4 líneas), en español rioplatense, firmado como "Consultorio Dr. Nicolás \
Buso" -- mismo estilo que ya usa el sistema de recordatorios.
4. Si es ambiguo, el borrador pide que aclare qué necesita, sin asumir nada."""

MENSAJE_WHATSAPP_JSON_SCHEMA = {
    "name": "clasificacion_mensaje_whatsapp",
    "schema": {
        "type": "object",
        "properties": {
            "clasificacion": {
                "type": "string",
                "enum": ["turno_nuevo", "cancelacion", "reprogramacion", "consulta_general", "receta", "ambiguo"],
            },
            "requiere_profesional": {"type": "boolean"},
            "urgente": {"type": "boolean"},
            "borrador_respuesta": {"type": "string"},
        },
        "required": ["clasificacion", "requiere_profesional", "urgente", "borrador_respuesta"],
        "additionalProperties": False,
    },
    "strict": True,
}

_REQUIRED_MENSAJE_KEYS = set(MENSAJE_WHATSAPP_JSON_SCHEMA["schema"]["required"])
_VALID_CLASIFICACIONES = set(MENSAJE_WHATSAPP_JSON_SCHEMA["schema"]["properties"]["clasificacion"]["enum"])


def _validate_mensaje_shape(data):
    if not isinstance(data, dict):
        return f"la respuesta no es un objeto JSON (vino {type(data).__name__})"
    missing = _REQUIRED_MENSAJE_KEYS - set(data.keys())
    if missing:
        return f"faltan campos requeridos: {', '.join(sorted(missing))}"
    if data.get("clasificacion") not in _VALID_CLASIFICACIONES:
        return f"'clasificacion' fuera del rango esperado: {data.get('clasificacion')!r}"
    if not isinstance(data.get("requiere_profesional"), bool):
        return "'requiere_profesional' no es booleano"
    if not isinstance(data.get("urgente"), bool):
        return "'urgente' no es booleano"
    if not isinstance(data.get("borrador_respuesta"), str) or not data["borrador_respuesta"].strip():
        return "'borrador_respuesta' vacío o no es texto"
    return None


def _try_clasificar_mensaje(provider_name: str, texto: str) -> dict:
    if provider_name == "openai":
        client = _get_openai_client()
        if client is None:
            return {"status": "auth_error", "provider": "openai", "error": _openai_error}
        try:
            resp = client.chat.completions.create(
                model=os.getenv("OPENAI_MODEL", "gpt-5"),
                messages=[
                    {"role": "system", "content": MENSAJE_WHATSAPP_SYSTEM_PROMPT},
                    {"role": "user", "content": texto},
                ],
                response_format={"type": "json_schema", "json_schema": MENSAJE_WHATSAPP_JSON_SCHEMA},
            )
            raw_content = resp.choices[0].message.content
            try:
                data = json.loads(raw_content)
            except (json.JSONDecodeError, TypeError) as e:
                return {"status": "malformed", "provider": "openai", "error": f"JSON inválido: {e}"}
        except Exception as e:
            status = "auth_error" if _classify_exception(e) == "auth" else "transient_error"
            return {"status": status, "provider": "openai", "error": f"{type(e).__name__}: {e}"}
    else:
        client = _get_claude_client()
        if client is None:
            return {"status": "auth_error", "provider": "claude", "error": _claude_error}
        try:
            resp = client.messages.create(
                model=os.getenv("ANTHROPIC_MODEL", "claude-sonnet-5"),
                max_tokens=500,
                system=MENSAJE_WHATSAPP_SYSTEM_PROMPT,
                tools=[{
                    "name": "clasificacion_mensaje_whatsapp",
                    "description": "Clasificación y borrador de respuesta para un mensaje entrante de WhatsApp.",
                    "input_schema": MENSAJE_WHATSAPP_JSON_SCHEMA["schema"],
                }],
                tool_choice={"type": "tool", "name": "clasificacion_mensaje_whatsapp"},
                messages=[{"role": "user", "content": texto}],
            )
            tool_block = next((b for b in resp.content if b.type == "tool_use"), None)
            if tool_block is None:
                return {"status": "malformed", "provider": "claude", "error": "la respuesta no incluyó el tool_use esperado"}
            data = tool_block.input
        except Exception as e:
            status = "auth_error" if _classify_exception(e) == "auth" else "transient_error"
            return {"status": status, "provider": "claude", "error": f"{type(e).__name__}: {e}"}

    shape_error = _validate_mensaje_shape(data)
    if shape_error:
        return {"status": "malformed", "provider": provider_name, "error": shape_error}
    return {"status": "success", "provider": provider_name, "data": data}


def clasificar_y_redactar_mensaje(texto: str, provider: str = None) -> dict:
    """Mismo contrato de salida que `extract()` -- 'outcome': 'success' |
    'auth_error' | 'both_failed'. `data` trae clasificacion/requiere_profesional/
    urgente/borrador_respuesta. Presupuesto duro: 2 llamadas HTTP reales."""
    provider = provider or get_provider_for("clasificar_mensaje_whatsapp", default="claude")
    alternate = "claude" if provider == "openai" else "openai"

    attempts = []
    first = _try_clasificar_mensaje(provider, texto)
    attempts.append(first)

    if first["status"] == "success":
        return {"outcome": "success", "data": first["data"], "provider": provider, "attempts": attempts}

    if first["status"] == "auth_error":
        return {"outcome": "auth_error", "provider": provider, "error": first["error"], "attempts": attempts}

    if not get_provider_matrix().get("fallback_on_primary_failure", True):
        return {"outcome": "both_failed", "error": first["error"], "attempts": attempts}

    second = _try_clasificar_mensaje(alternate, texto)
    attempts.append(second)

    if second["status"] == "success":
        return {
            "outcome": "success", "data": second["data"], "provider": alternate,
            "fell_back_from": provider, "attempts": attempts,
        }

    combined_error = f"{provider}: {first['error']} | {alternate}: {second['error']}"
    return {"outcome": "both_failed", "error": combined_error, "attempts": attempts}


def review(candidate_text: str, context: dict, provider: str = None) -> dict:
    """Un segundo proveedor revisa una salida antes de mostrarla — usado para
    control cruzado en tareas donde conviene una segunda mirada (ej. redacción
    de un informe), NO para decidir si algo se ejecuta."""
    provider = provider or get_provider_for("review", default="claude")
    question = (
        "Revisá este texto candidato y decime si tiene algún error de hecho contra el "
        f"contexto real, o si está bien tal cual:\n\n{candidate_text}"
    )
    if provider == "openai":
        return _ask_openai(question, context, history=[])
    return _ask_claude(question, context, history=[])
