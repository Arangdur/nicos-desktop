"""
Cliente de Gmail para las dos casillas del módulo de mail: consultorio
(novogen.salud@gmail.com) y Fundación Abate (fundacion.abate@gmail.com).
Lee mails nuevos y manda la respuesta ya aprobada -- nunca decide nada,
solo ejecuta lo que mail_entrante.py le pide después de la aprobación del
Director.

Por qué Gmail API (google-api-python-client + google-auth) y no la vía de
cuenta de servicio que usaba el viejo sheets_client.py: las dos casillas
son Gmail comunes, no Google Workspace -- una cuenta de servicio con
delegación de dominio (lo que hacía falta para Sheets) no aplica acá.
Gmail API con OAuth "Desktop app" + refresh token es la vía correcta para
leer/enviar en nombre de una cuenta Gmail normal sin pedir la contraseña
ni loguearse cada vez.

Setup real, en dos partes:
1. Nicolás crea un proyecto en Google Cloud Console, habilita la Gmail
   API, y genera credenciales OAuth tipo "Desktop app" -- esto le da un
   client_id y un client_secret (uno solo, se reusa para las dos
   casillas).
2. Con esas credenciales, corre `python3 sidecar/gmail_oauth_setup.py`
   UNA VEZ POR CASILLA (logueado con esa cuenta de Gmail en el navegador
   que se abre) -- el script imprime un refresh_token que se pega en
   Ajustes del Director. Ningún paso de este archivo puede completar esa
   parte -- necesita su login real en el navegador, no hay forma de
   automatizarlo sin eso.
"""
import base64
import os
from email.mime.text import MIMEText

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
TOKEN_URI = "https://oauth2.googleapis.com/token"

CASILLAS = {
    "consultorio": "novogen.salud@gmail.com",
    "abate": "fundacion.abate@gmail.com",
}


class GmailConfigError(Exception):
    pass


class GmailSendError(Exception):
    pass


def _config(casilla: str) -> dict:
    if casilla not in CASILLAS:
        raise GmailConfigError(f"Casilla desconocida: {casilla!r} (válidas: {', '.join(CASILLAS)})")
    client_id = os.getenv("GMAIL_CLIENT_ID")
    client_secret = os.getenv("GMAIL_CLIENT_SECRET")
    # Un refresh token por casilla -- son cuentas de Gmail distintas, cada
    # una consintió el acceso por separado (ver gmail_oauth_setup.py).
    refresh_token = os.getenv(f"GMAIL_REFRESH_TOKEN_{casilla.upper()}")
    if not client_id or not client_secret or not refresh_token:
        raise GmailConfigError(
            f"Falta configurar Gmail para la casilla '{casilla}' ({CASILLAS[casilla]}) -- "
            "cargá GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET y el refresh token de esta casilla "
            "en Ajustes del Director. Ver el comentario de setup en gmail_client.py."
        )
    return {"client_id": client_id, "client_secret": client_secret, "refresh_token": refresh_token}


def _service(casilla: str):
    cfg = _config(casilla)
    creds = Credentials(
        token=None,
        refresh_token=cfg["refresh_token"],
        token_uri=TOKEN_URI,
        client_id=cfg["client_id"],
        client_secret=cfg["client_secret"],
        scopes=SCOPES,
    )
    # El access token siempre arranca vencido (token=None) -- se renueva acá
    # mismo con el refresh token, una vez por llamada. Gmail API no permite
    # cachear esto entre ticks del worker de forma simple sin agregar más
    # estado del que vale la pena para el volumen real de estas casillas
    # (bajo, según Nicolás).
    creds.refresh(Request())
    return build("gmail", "v1", credentials=creds, cache_discovery=False)


def _decodificar_body(payload: dict) -> str:
    """Un mail real puede venir como texto plano directo, o multipart
    (texto plano + HTML). Se prioriza siempre 'text/plain' -- nunca hace
    falta parsear HTML acá, solo dar a la IA algo legible."""
    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode("utf-8", errors="replace")

    for part in payload.get("parts", []) or []:
        if part.get("mimeType") == "text/plain" and part.get("body", {}).get("data"):
            return base64.urlsafe_b64decode(part["body"]["data"]).decode("utf-8", errors="replace")
    # Fallback: multipart anidado (ej. multipart/alternative dentro de multipart/mixed
    # por un adjunto) -- se busca un nivel más adentro antes de rendirse.
    for part in payload.get("parts", []) or []:
        if part.get("parts"):
            texto = _decodificar_body(part)
            if texto:
                return texto
    return ""


def _header(headers: list, nombre: str) -> str:
    for h in headers:
        if h.get("name", "").lower() == nombre.lower():
            return h.get("value", "")
    return ""


def list_mensajes_nuevos(casilla: str, max_resultados: int = 20) -> list:
    """Trae los mails más recientes de la bandeja de entrada -- el
    deduplicado real (no volver a registrar un mail ya visto) lo hace
    mail_entrante.py contra gmail_message_id, no acá. Solo lee, nunca
    marca como leído ni mueve nada."""
    service = _service(casilla)
    try:
        resultado = service.users().messages().list(
            userId="me", labelIds=["INBOX"], maxResults=max_resultados,
        ).execute()
    except HttpError as e:
        raise GmailConfigError(f"Gmail no respondió al listar mensajes de '{casilla}': {e}")

    mensajes = []
    for item in resultado.get("messages", []):
        try:
            full = service.users().messages().get(userId="me", id=item["id"], format="full").execute()
        except HttpError as e:
            # Un mensaje puntual que falla no debería tirar abajo el resto
            # del lote -- se salta y se loguea, el próximo tick lo reintenta.
            import sys
            sys.stderr.write(f"[gmail_client] no se pudo leer el mensaje {item['id']} de '{casilla}': {e}\n")
            continue
        headers = full.get("payload", {}).get("headers", [])
        mensajes.append({
            "gmail_message_id": full["id"],
            "thread_id": full.get("threadId"),
            "remitente": _header(headers, "From"),
            "asunto": _header(headers, "Subject"),
            "cuerpo": _decodificar_body(full.get("payload", {})).strip(),
        })
    return mensajes


def enviar_respuesta(casilla: str, destinatario: str, asunto: str, cuerpo: str, thread_id: str = None) -> dict:
    """Manda la respuesta YA APROBADA por el Director -- este archivo no
    decide nada, solo ejecuta. Si hay thread_id, responde dentro del mismo
    hilo (mismo criterio que cualquier cliente de mail real)."""
    service = _service(casilla)
    mensaje = MIMEText(cuerpo)
    mensaje["to"] = destinatario
    mensaje["from"] = CASILLAS[casilla]
    mensaje["subject"] = asunto if asunto.lower().startswith("re:") else f"Re: {asunto}"
    raw = base64.urlsafe_b64encode(mensaje.as_bytes()).decode("ascii")
    body = {"raw": raw}
    if thread_id:
        body["threadId"] = thread_id
    try:
        enviado = service.users().messages().send(userId="me", body=body).execute()
    except HttpError as e:
        raise GmailSendError(f"Gmail no pudo mandar la respuesta desde '{casilla}': {e}")
    return {"id": enviado.get("id")}
