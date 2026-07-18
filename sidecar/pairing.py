"""
Pairing seguro Mac <-> PC de Marianela. Reemplaza cualquier contraseña fija
compartida: un código corto de un solo uso (5 min) autoriza la emisión de un
token de dispositivo de 256 bits, revocable individualmente. El token en sí
nunca se guarda en la base — solo su hash (misma lógica que una contraseña),
así que un volcado de nicos.db no expone tokens utilizables.

Rate limiting (v0.2.1): 5+ intentos fallidos contra el mismo código en los
últimos 5 minutos lo invalida aunque no haya vencido su TTL — cada intento
(éxito o fracaso) queda en `pairing_attempts`.
"""
import datetime
import hashlib
import hmac
import secrets

import db

PAIRING_CODE_TTL_MINUTES = 5
MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPTS_WINDOW_MINUTES = 5


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


def _record_attempt(code: str, success: bool):
    conn = db.get_connection()
    conn.execute(
        "INSERT INTO pairing_attempts (code_attempted, success, created_at) VALUES (?, ?, ?)",
        (code, 1 if success else 0, _now_iso()),
    )
    conn.commit()


def _too_many_failed_attempts(code: str) -> bool:
    conn = db.get_connection()
    cutoff = (datetime.datetime.utcnow() - datetime.timedelta(minutes=FAILED_ATTEMPTS_WINDOW_MINUTES)).isoformat()
    row = conn.execute(
        "SELECT COUNT(*) as n FROM pairing_attempts WHERE code_attempted = ? AND success = 0 AND created_at > ?",
        (code, cutoff),
    ).fetchone()
    return row["n"] >= MAX_FAILED_ATTEMPTS


def complete_pairing(code: str, device_name: str, user_id: str = "marianela") -> dict:
    """Valida el código (sin usar, no vencido, sin demasiados intentos fallidos
    recientes) y emite un token nuevo de dispositivo. El token se devuelve UNA
    sola vez acá — el llamador es responsable de guardarlo de forma segura
    (safeStorage en el cliente Electron); el server nunca lo vuelve a mostrar,
    solo puede verificar su hash a futuro.
    """
    if _too_many_failed_attempts(code):
        _record_attempt(code, success=False)
        raise PairingError(
            f"Demasiados intentos fallidos con este código ({MAX_FAILED_ATTEMPTS}+ en "
            f"{FAILED_ATTEMPTS_WINDOW_MINUTES} min) — se invalidó por seguridad. Generá uno nuevo desde Ajustes."
        )

    conn = db.get_connection()
    row = conn.execute("SELECT * FROM pairing_codes WHERE code = ?", (code,)).fetchone()
    if row is None:
        _record_attempt(code, success=False)
        raise PairingError("Código inválido.")
    if row["used_at"] is not None:
        _record_attempt(code, success=False)
        raise PairingError("Este código ya se usó — generá uno nuevo desde Ajustes.")
    if datetime.datetime.fromisoformat(row["expires_at"]) < datetime.datetime.utcnow():
        _record_attempt(code, success=False)
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
    _record_attempt(code, success=True)

    return {"device_id": device_id, "token": token}


def verify_token(token: str):
    """Devuelve la fila de `devices` si el token es válido y no está revocado, si no None.
    Comparación con hmac.compare_digest (tiempo constante) por defensa en profundidad,
    aunque el token ya viaja dentro de la red cifrada de Tailscale."""
    if not token:
        return None
    conn = db.get_connection()
    token_hash = _hash_token(token)
    for row in conn.execute("SELECT * FROM devices WHERE revoked_at IS NULL"):
        if hmac.compare_digest(row["token_hash"], token_hash):
            return row
    return None


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
