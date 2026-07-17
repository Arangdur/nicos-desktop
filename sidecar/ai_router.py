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
