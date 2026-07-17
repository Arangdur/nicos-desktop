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
        return {"version": 0, "rules": {}, "fallback_allowed_for_simple_tasks": True}


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


def _extract_openai(raw_text: str) -> dict:
    client = _get_openai_client()
    if client is None:
        return {"ok": False, "error": _openai_error, "provider": "openai"}
    try:
        resp = client.chat.completions.create(
            model=os.getenv("OPENAI_MODEL", "gpt-5"),
            messages=[
                {"role": "system", "content": EXTRACTION_SYSTEM_PROMPT},
                {"role": "user", "content": raw_text},
            ],
            response_format={"type": "json_schema", "json_schema": EXTRACTION_JSON_SCHEMA},
        )
        data = json.loads(resp.choices[0].message.content)
        return {"ok": True, "data": data, "provider": "openai"}
    except Exception as e:
        return {"ok": False, "error": f"Error extrayendo con OpenAI: {e}", "provider": "openai"}


def _extract_claude(raw_text: str) -> dict:
    client = _get_claude_client()
    if client is None:
        return {"ok": False, "error": _claude_error, "provider": "claude"}
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
        tool_block = next(b for b in resp.content if b.type == "tool_use")
        return {"ok": True, "data": tool_block.input, "provider": "claude"}
    except Exception as e:
        return {"ok": False, "error": f"Error extrayendo con Claude: {e}", "provider": "claude"}


def extract(raw_text: str, provider: str = None) -> dict:
    """Convierte texto libre en {domain, intent, amount, date, concept, evidence}.
    El proveedor se elige por la matriz versionada (provider_matrix.json) salvo
    que se pase explícitamente. Nunca decide riesgo — eso es de centro_mando_adapter.py."""
    provider = provider or get_provider_for("extract_task", default="openai")
    if provider == "openai":
        result = _extract_openai(raw_text)
        if not result["ok"] and get_provider_matrix().get("fallback_allowed_for_simple_tasks", True):
            # la extracción en sí no es una acción riesgosa (no ejecuta nada) — el
            # fallback acá es seguro; lo que nunca hace fallback es execute_action.
            fallback = _extract_claude(raw_text)
            fallback["fell_back_from"] = "openai"
            return fallback
        return result
    return _extract_claude(raw_text)


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
