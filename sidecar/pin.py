"""
PIN de 4 dígitos para el selector "¿Quién sos?" en un dispositivo compartido
por varias personas (ej. la PC de enfermería de Abate, entre Carlos y María).

Deliberadamente NO es el mecanismo de seguridad real de NicOS -- ese sigue
siendo el token de dispositivo de pairing.py, autorizado solo dentro de la
red de Tailscale. El PIN existe para evitar que, en un dispositivo ya
autorizado, alguien cargue una novedad a nombre de otro turno por error de
click -- no para resistir un atacante. Aun así se guarda hasheado (nunca en
texto plano) por el mismo principio general que un token o una contraseña.
"""
import hashlib
import hmac
import re

PIN_PATTERN = re.compile(r"^\d{4}$")


class InvalidPinError(Exception):
    pass


def validate_pin_format(pin: str):
    if not PIN_PATTERN.match(pin or ""):
        raise InvalidPinError("El PIN debe ser de exactamente 4 dígitos numéricos.")


def hash_pin(pin: str) -> str:
    validate_pin_format(pin)
    return hashlib.sha256(pin.encode("utf-8")).hexdigest()


def verify_pin(pin: str, pin_hash: str) -> bool:
    if not pin or not pin_hash:
        return False
    return hmac.compare_digest(hashlib.sha256(pin.encode("utf-8")).hexdigest(), pin_hash)
