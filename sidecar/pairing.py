"""
Pairing seguro Mac <-> PC de Marianela. Reemplaza cualquier contraseña fija
compartida: un código corto de un solo uso (5 min) autoriza la emisión de un
token de dispositivo de 256 bits, revocable individualmente. El token en sí
nunca se guarda en la base — solo su hash (misma lógica que una contraseña),
así que un volcado de nicos.db no expone tokens utilizables.
"""
import datetime
import hashlib
import secrets

import db

PAIRING_CODE_TTL_MINUTES = 5


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def start_pairing() -> dict:
    """Genera un código de 6 dígitos válido 5 minutos. Solo se llama desde la
    vista Director (ruta protegida a nivel de UI: el botón vive en Ajustes)."""
    conn = db.get_connection()
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.datetime.utcnow()
    expires = now + datetime.timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    conn.execute(
        "INSERT INTO pairing_codes (code, created_at, expires_at) VALUES (?, ?, ?)",
        (code, now.isoformat(), expires.isoformat()),
    )
    conn.commit()
    return {"code": code, "expires_at": expires.isoformat()}


class PairingError(Exception):
    pass


def complete_pairing(code: str, device_name: str, user_id: str = "marianela") -> dict:
    """Valida el código (sin usar, no vencido) y emite un token nuevo de dispositivo.
    El token se devuelve UNA sola vez acá — el llamador es responsable de guardarlo
    de forma segura (safeStorage en el cliente Electron); el server nunca lo vuelve
    a mostrar, solo puede verificar su hash a futuro.
    """
    conn = db.get_connection()
    row = conn.execute("SELECT * FROM pairing_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        raise PairingError("Código inválido.")
    if row["used_at"] is not None:
        raise PairingError("Este código ya se usó — generá uno nuevo desde Ajustes.")
    if datetime.datetime.fromisoformat(row["expires_at"]) < datetime.datetime.utcnow():
        raise PairingError("Este código venció (duran 5 minutos) — generá uno nuevo.")

    token = secrets.token_urlsafe(32)
    device_id = secrets.token_hex(8)
    now = _now_iso()

    conn.execute(
        "INSERT INTO devices (device_id, device_name, user_id, token_hash, paired_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (device_id, device_name, user_id, _hash_token(token), now),
    )
    conn.execute("UPDATE pairing_codes SET used_at = ? WHERE code = ?", (now, code))
    conn.commit()

    return {"device_id": device_id, "token": token}


def verify_token(token: str):
    """Devuelve la fila de `devices` si el token es válido y no está revocado, si no None."""
    if not token:
        return None
    conn = db.get_connection()
    row = conn.execute(
        "SELECT * FROM devices WHERE token_hash = ? AND revoked_at IS NULL",
        (_hash_token(token),),
    ).fetchone()
    return row


def list_devices():
    conn = db.get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT device_id, device_name, user_id, paired_at, revoked_at FROM devices ORDER BY paired_at DESC"
    ).fetchall()]


def revoke_device(device_id: str):
    conn = db.get_connection()
    conn.execute(
        "UPDATE devices SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
        (_now_iso(), device_id),
    )
    conn.commit()
