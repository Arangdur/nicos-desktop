"""
Verificación de la firma de Twilio para el webhook de WhatsApp ENTRANTE
(v0.2.5) -- la única ruta del sidecar que puede recibir tráfico real de
internet (vía Tailscale Funnel), así que nunca usa el modelo `is_lan()` del
resto de las rutas admin. La seguridad acá es la firma HMAC, no la topología
de red: cualquiera puede llegar a esta URL, pero solo una request realmente
firmada por Twilio con el Auth Token real pasa la verificación.

Algoritmo documentado por Twilio (no HMAC-SHA256 -- Twilio usa SHA1 para esto,
distinto del HMAC-SHA256 que usa DrApp para sus propios webhooks, no confundir
los dos): tomar la URL completa tal como la configuramos en la consola de
Twilio, concatenarle cada parámetro del POST ordenado alfabéticamente por
nombre (nombre+valor pegado, sin separador), firmar con HMAC-SHA1 usando el
Auth Token como clave, base64, comparar contra el header X-Twilio-Signature
con una comparación de tiempo constante (`hmac.compare_digest`).
"""
import base64
import hashlib
import hmac
from urllib.parse import parse_qsl


class TwilioSignatureError(Exception):
    pass


def verificar_firma(url: str, form_params: dict, signature: str, auth_token: str) -> bool:
    """`url` tiene que ser EXACTAMENTE la URL pública que Twilio tiene
    configurada para este webhook (incluye https:// y el dominio del Funnel
    de Tailscale) -- si no coincide carácter a carácter, la firma nunca va a
    dar bien aunque el request sea legítimo. `form_params` son los campos del
    body application/x-www-form-urlencoded tal cual llegaron (From, Body,
    MessageSid, etc.)."""
    if not signature or not auth_token:
        return False
    data = url
    for key in sorted(form_params.keys()):
        data += key + str(form_params[key])
    computed = base64.b64encode(
        hmac.new(auth_token.encode("utf-8"), data.encode("utf-8"), hashlib.sha1).digest()
    ).decode("utf-8")
    return hmac.compare_digest(computed, signature)


def parsear_form_body(raw_body: bytes) -> dict:
    """Twilio manda application/x-www-form-urlencoded, no JSON -- esto es lo
    que separa esta ruta del resto de `do_POST` (que asume JSON siempre)."""
    texto = raw_body.decode("utf-8")
    return {k: v for k, v in parse_qsl(texto, keep_blank_values=True)}


def extraer_telefono_y_texto(form_params: dict):
    """Twilio manda el remitente como 'whatsapp:+549...' -- acá se saca el
    prefijo para guardar el teléfono en el mismo formato que usa el resto de
    NicOS (recordatorios.py, twilio_client.py ya le agrega 'whatsapp:' él
    solo al mandar)."""
    from_raw = form_params.get("From", "")
    telefono = from_raw[len("whatsapp:"):] if from_raw.startswith("whatsapp:") else from_raw
    texto = form_params.get("Body", "").strip()
    return telefono, texto
