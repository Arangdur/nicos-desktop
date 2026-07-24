"""
Cliente mínimo de la API de Twilio para mandar WhatsApp -- alcanza con
urllib.request de la stdlib para esta única llamada REST, sin agregar
`requests` como dependencia nueva del paquete PyInstaller.
"""
import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request

REQUEST_TIMEOUT_SECONDS = 10


class TwilioConfigError(Exception):
    pass


class TwilioSendError(Exception):
    pass


def _config():
    account_sid = os.getenv("TWILIO_ACCOUNT_SID")
    auth_token = os.getenv("TWILIO_AUTH_TOKEN")
    whatsapp_from = os.getenv("TWILIO_WHATSAPP_FROM")
    if not account_sid or not auth_token or not whatsapp_from:
        raise TwilioConfigError(
            "Falta configurar Twilio (Account SID, Auth Token, número de WhatsApp) "
            "en la pantalla de Ajustes de la app."
        )
    return account_sid, auth_token, whatsapp_from


def enviar_whatsapp(to: str, body: str):
    """`to` = número del paciente sin el prefijo whatsapp: (se agrega acá).
    Devuelve el sid del mensaje si Twilio lo aceptó para envío -- eso NO
    garantiza que ya llegó al teléfono del paciente, solo que Twilio lo tomó."""
    account_sid, auth_token, whatsapp_from = _config()

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = urllib.parse.urlencode({
        "From": f"whatsapp:{whatsapp_from}",
        "To": f"whatsapp:{to}",
        "Body": body,
    }).encode("utf-8")

    credentials = base64.b64encode(f"{account_sid}:{auth_token}".encode("utf-8")).decode("ascii")
    request = urllib.request.Request(url, data=data, method="POST")
    request.add_header("Authorization", f"Basic {credentials}")
    request.add_header("Content-Type", "application/x-www-form-urlencoded")

    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT_SECONDS) as response:
            result = json.loads(response.read().decode("utf-8"))
            return {"sid": result.get("sid"), "status": result.get("status")}
    except urllib.error.HTTPError as e:
        detail = e.read().decode("utf-8", errors="replace")
        raise TwilioSendError(f"Twilio rechazó el envío ({e.code}): {detail}")
    except urllib.error.URLError as e:
        raise TwilioSendError(f"No se pudo conectar con Twilio: {e.reason}")
