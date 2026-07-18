"""
Clientes falsos de Anthropic/OpenAI para las pruebas de resiliencia de
extracción (v0.2.1-rc6) -- nunca pegan a una API real. Se inyectan
monkeypatcheando `ai_router._get_claude_client` / `_get_openai_client`.

`_classify_exception()` en ai_router.py clasifica por NOMBRE de clase de la
excepción (Anthropic y OpenAI exponen los mismos nombres: AuthenticationError,
RateLimitError, etc.), así que estas excepciones falsas -- con los mismos
nombres, sin heredar de las librerías reales -- alcanzan para probar la
clasificación sin depender de los constructores reales del SDK (que piden
objetos `response`/`body` que no vale la pena simular acá).

Cada cliente falso recibe una lista `behavior`: una entrada por llamada
esperada (se repite la última si se llama más veces de las previstas, para no
explotar por un IndexError en vez de fallar el assert real del test). Cada
entrada es:
  - una instancia de Exception -> se levanta.
  - la cadena literal "malformed" -> respuesta que no matchea el schema.
  - un dict -> los datos "extraídos" (formato correcto).
"""
import json


class AuthenticationError(Exception):
    pass


class RateLimitError(Exception):
    pass


class _FakeToolBlock:
    type = "tool_use"

    def __init__(self, input_):
        self.input = input_


class _FakeAnthropicResponse:
    def __init__(self, tool_input):
        self.content = [_FakeToolBlock(tool_input)]


class _FakeAnthropicResponseNoToolUse:
    content = []


class _FakeAnthropicMessages:
    def __init__(self, behavior):
        self.behavior = behavior
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        action = self.behavior[min(self.call_count - 1, len(self.behavior) - 1)]
        if isinstance(action, Exception):
            raise action
        if action == "malformed":
            return _FakeAnthropicResponseNoToolUse()
        return _FakeAnthropicResponse(action)


class FakeAnthropicClient:
    def __init__(self, behavior):
        self.messages = _FakeAnthropicMessages(behavior)

    @property
    def call_count(self):
        return self.messages.call_count


class _FakeOpenAIMessage:
    def __init__(self, content):
        self.content = content


class _FakeOpenAIChoice:
    def __init__(self, content):
        self.message = _FakeOpenAIMessage(content)


class _FakeOpenAIResponse:
    def __init__(self, content):
        self.choices = [_FakeOpenAIChoice(content)]


class _FakeOpenAICompletions:
    def __init__(self, behavior):
        self.behavior = behavior
        self.call_count = 0

    def create(self, **kwargs):
        self.call_count += 1
        action = self.behavior[min(self.call_count - 1, len(self.behavior) - 1)]
        if isinstance(action, Exception):
            raise action
        if action == "malformed":
            return _FakeOpenAIResponse("esto no es json valido {{{")
        return _FakeOpenAIResponse(json.dumps(action))


class _FakeOpenAIChat:
    def __init__(self, behavior):
        self.completions = _FakeOpenAICompletions(behavior)


class FakeOpenAIClient:
    def __init__(self, behavior):
        self.chat = _FakeOpenAIChat(behavior)

    @property
    def call_count(self):
        return self.chat.completions.call_count


def valid_extraction(domain="cfo", intent="register_expense", amount=1000,
                      date="18/07/2026", concept="algo", evidence=None):
    return {
        "domain": domain, "intent": intent, "amount": amount,
        "date": date, "concept": concept, "evidence": evidence,
    }


def install_fake_clients(ai_router_mod, claude_behavior=None, openai_behavior=None):
    """Reemplaza _get_claude_client/_get_openai_client por versiones que
    devuelven clientes falsos con el `behavior` dado. Si algún behavior es
    None, ese proveedor se comporta como si no tuviera key configurada (client
    is None) -- 'auth_error' sin gastar presupuesto de llamada."""
    claude_client = FakeAnthropicClient(claude_behavior) if claude_behavior is not None else None
    openai_client = FakeOpenAIClient(openai_behavior) if openai_behavior is not None else None

    ai_router_mod._get_claude_client = lambda: claude_client
    ai_router_mod._get_openai_client = lambda: openai_client
    ai_router_mod._claude_error = "Falta configurar la API key de Anthropic en Ajustes." if claude_client is None else None
    ai_router_mod._openai_error = "Falta configurar la API key de OpenAI en Ajustes." if openai_client is None else None

    return claude_client, openai_client
