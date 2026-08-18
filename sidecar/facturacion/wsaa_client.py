"""
Cliente de WSAA (Web Service de Autenticación y Autorización) de ARCA/AFIP.

Antes de poder pedir un CAE en WSFE, hay que autenticarse acá: se arma un
XML "Login Ticket Request" pidiendo acceso al servicio "wsfe", se firma con
el certificado de Nicolás (CMS/PKCS#7, vía `openssl smime` -- es el método
estándar, no hay una librería Python liviana que lo haga bien sin agregar
una dependencia pesada como lxml/zeep), se manda a WSAA y devuelve un
`token`+`sign` válidos por 12 horas.

ponytail: shellea a `openssl smime -sign` en vez de usar
cryptography.hazmat.primitives.serialization.pkcs7 -- la API de firma CMS de
`cryptography` es más nueva y menos probada contra las rarezas de ARCA que
el comando de openssl, que es lo que usa la enorme mayoría de clientes WSAA
en producción. Si en el futuro se agrega `cryptography`-only signing,
reemplazar acá sin tocar el resto del módulo.
"""
import datetime
import subprocess
import tempfile
import time
import uuid
import xml.etree.ElementTree as ET

import requests

WSAA_URL = "https://wsaa.afip.gov.ar/ws/services/LoginCms"
SERVICE = "wsfe"

# Cache en memoria del proceso -- el token dura 12hs, no hace falta pedir uno
# nuevo en cada factura. Si el sidecar se reinicia, se pide uno nuevo, no pasa
# nada (WSAA no tiene límite de logins razonable para este volumen de uso).
_cache = {"token": None, "sign": None, "expira": None}


class WsaaError(Exception):
    pass


def _iso_con_colon(dt: datetime.datetime) -> str:
    """xsd:dateTime exige el offset de timezone con ':' (ej. -03:00) --
    strftime('%z') da '-0300' sin colon, y ARCA rechaza el XML entero contra
    el schema si falta (así se manifestó la primera vez: 'xml.bad')."""
    s = dt.strftime("%Y-%m-%dT%H:%M:%S%z")
    return s[:-2] + ":" + s[-2:]


def _armar_login_ticket_request() -> str:
    ahora = datetime.datetime.now(datetime.timezone.utc).astimezone()
    generation = ahora - datetime.timedelta(minutes=5)  # margen por reloj desincronizado
    expiration = ahora + datetime.timedelta(minutes=10)
    unique_id = str(int(time.time()))
    return f"""<?xml version="1.0" encoding="UTF-8"?>
<loginTicketRequest version="1.0">
  <header>
    <uniqueId>{unique_id}</uniqueId>
    <generationTime>{_iso_con_colon(generation)}</generationTime>
    <expirationTime>{_iso_con_colon(expiration)}</expirationTime>
  </header>
  <service>{SERVICE}</service>
</loginTicketRequest>"""


def _firmar_cms(xml_texto: str, cert_path: str, key_path: str) -> str:
    """Firma el XML con el certificado+clave privada, devuelve el CMS en
    base64 (sin las cabeceras MIME que agrega `openssl smime` por default --
    WSAA quiere el base64 pelado)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".xml", delete=False) as f:
        f.write(xml_texto)
        xml_path = f.name
    try:
        resultado = subprocess.run(
            [
                "openssl", "smime", "-sign",
                "-signer", cert_path,
                "-inkey", key_path,
                "-outform", "DER",
                "-nodetach",
                "-in", xml_path,
            ],
            capture_output=True, check=True,
        )
    except subprocess.CalledProcessError as e:
        raise WsaaError(f"Falló la firma CMS con openssl: {e.stderr.decode('utf-8', 'replace')}")
    finally:
        import os
        os.unlink(xml_path)
    import base64
    return base64.b64encode(resultado.stdout).decode("ascii")


def _pedir_ticket(cms_base64: str) -> tuple:
    """Devuelve (token, sign, expirationTime) parseados de la respuesta SOAP."""
    soap_body = f"""<?xml version="1.0" encoding="UTF-8"?>
<soapenv:Envelope xmlns:soapenv="http://schemas.xmlsoap.org/soap/envelope/" xmlns:wsaa="https://wsaa.afip.gov.ar/ws/services/LoginCms">
  <soapenv:Header/>
  <soapenv:Body>
    <wsaa:loginCms>
      <wsaa:in0>{cms_base64}</wsaa:in0>
    </wsaa:loginCms>
  </soapenv:Body>
</soapenv:Envelope>"""
    resp = requests.post(
        WSAA_URL,
        data=soap_body.encode("utf-8"),
        headers={"Content-Type": "text/xml; charset=utf-8", "SOAPAction": ""},
        timeout=30,
    )
    if resp.status_code != 200:
        raise WsaaError(f"WSAA respondió HTTP {resp.status_code}: {resp.text[:500]}")

    envelope = ET.fromstring(resp.text)
    # loginCmsReturn viene como texto XML escapado dentro del SOAP -- hay que
    # reparsearlo como su propio documento.
    ns = {"soapenv": "http://schemas.xmlsoap.org/soap/envelope/"}
    body = envelope.find("soapenv:Body", ns)
    fault = body.find("soapenv:Fault", ns) if body is not None else None
    if fault is not None:
        faultstring = fault.findtext("faultstring", default="error desconocido de WSAA")
        raise WsaaError(f"WSAA rechazó el login: {faultstring}")

    return_text = None
    for elem in body.iter():
        if elem.tag.endswith("loginCmsReturn"):
            return_text = elem.text
            break
    if not return_text:
        raise WsaaError(f"Respuesta de WSAA sin loginCmsReturn: {resp.text[:500]}")

    ticket = ET.fromstring(return_text)
    token = ticket.findtext(".//token")
    sign = ticket.findtext(".//sign")
    expiration = ticket.findtext(".//header/expirationTime")
    if not token or not sign:
        raise WsaaError(f"Ticket de WSAA sin token/sign: {return_text[:500]}")
    return token, sign, expiration


def obtener_credenciales(cert_path: str, key_path: str, forzar: bool = False) -> dict:
    """Punto de entrada del módulo. Devuelve {'token': ..., 'sign': ...},
    usando el caché si todavía es válido (con 10 minutos de margen antes de
    que expire, para no arrancar una factura con un token a punto de vencer)."""
    ahora = time.time()
    if not forzar and _cache["token"] and _cache["expira"] and ahora < _cache["expira"] - 600:
        return {"token": _cache["token"], "sign": _cache["sign"]}

    xml_req = _armar_login_ticket_request()
    cms = _firmar_cms(xml_req, cert_path, key_path)
    token, sign, expiration_iso = _pedir_ticket(cms)

    try:
        expira_dt = datetime.datetime.fromisoformat(expiration_iso)
        _cache["expira"] = expira_dt.timestamp()
    except (ValueError, TypeError):
        _cache["expira"] = ahora + 11 * 3600  # fallback conservador si el parseo falla

    _cache["token"] = token
    _cache["sign"] = sign
    return {"token": token, "sign": sign}
