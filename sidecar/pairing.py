"""
Pairing seguro Mac <-> PC de quien se vincule (Operativa, Enfermero/a de
Abate, futuros roles). Reemplaza cualquier contraseña fija compartida: un
código corto de un solo uso (5 min) autoriza la emisión de un token de
dispositivo de 256 bits, revocable individualmente. El token en sí nunca se
guarda en la base — solo su hash (misma lógica que una contraseña), así que
un volcado de nicos.db no expone tokens utilizables.

v0.2.2 -- el rol ya NO es un valor fijo ("marianela" hardcodeado): el
Director elige rol (y turno, si aplica) AL GENERAR el código, en
start_pairing(). La persona nunca elige su propio rol -- solo completa sus
datos personales (nombre, DNI, fecha de nacimiento, sexo) y un PIN de sesión
en complete_pairing(), que es quien efectivamente crea su fila en `users`
(antes de esto, `users` era una tabla semilla de solo 2 filas fijas -- ahora
es un catálogo abierto que crece con cada alta real).

Rate limiting (v0.2.1): 5+ intentos fallidos contra el mismo código en los
últimos 5 minutos lo invalida aunque no haya vencido su TTL — cada intento
(éxito o fracaso) queda en `pairing_attempts`.
"""
import datetime
import hashlib
import hmac
import re
import secrets
import unicodedata

import db
import pin as pin_module

PAIRING_CODE_TTL_MINUTES = 5
MAX_FAILED_ATTEMPTS = 5
FAILED_ATTEMPTS_WINDOW_MINUTES = 5

# Roles que el Director puede asignar AL GENERAR un código de vinculación.
# 'director' no está acá a propósito -- Nicolás nunca se vincula por código,
# su Mac arranca directo en ese rol (ver electron/main.js). Agregar un rol
# nuevo el día de mañana es sumar una línea acá, no una migración de esquema.
PAIRING_ASSIGNABLE_ROLES = {"operativa", "enfermero"}
TURNOS_VALIDOS = {"manana", "tarde", "noche", "rotativo", None, ""}
DNI_PATTERN = re.compile(r"^\d{6,9}$")


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _slugify(display_name: str) -> str:
    ascii_name = unicodedata.normalize("NFKD", display_name).encode("ascii", "ignore").decode("ascii")
    first_word = re.sub(r"[^a-zA-Z]", "", ascii_name.split()[0]) if ascii_name.split() else "persona"
    return (first_word or "persona").lower()


def _generate_user_id(display_name: str) -> str:
    """Legible (para leer en logs/DB a mano) pero único -- dos 'Carlos' no
    colisionan porque cada uno lleva un sufijo aleatorio corto."""
    conn = db.get_connection()
    base = _slugify(display_name)
    for _ in range(20):
        candidate = f"{base}-{secrets.token_hex(2)}"
        exists = conn.execute("SELECT 1 FROM users WHERE user_id = ?", (candidate,)).fetchone()
        if not exists:
            return candidate
    # Estadísticamente ~imposible con 20 intentos de 16 bits de espacio, pero
    # sin este fallback un bug de otro tipo podría convertirse en loop infinito.
    raise PairingError("No se pudo generar un identificador único -- reintentá el alta.")


class PairingError(Exception):
    pass


def start_pairing(role: str, turno: str = None, created_by: str = None, display_name: str = None) -> dict:
    """Genera un código de 6 dígitos válido 5 minutos, con el ROL (y turno, si
    aplica) ya decididos por el Director -- la persona que lo use después no
    elige nada de esto. Solo se llama desde la vista Director (ruta
    protegida también a nivel de servidor: is_lan() la bloquea en server.py)."""
    if role not in PAIRING_ASSIGNABLE_ROLES:
        raise PairingError(
            f"Rol '{role}' inválido -- los roles asignables por código son: "
            f"{', '.join(sorted(PAIRING_ASSIGNABLE_ROLES))}."
        )
    if turno not in TURNOS_VALIDOS:
        raise PairingError(f"Turno '{turno}' inválido.")

    conn = db.get_connection()
    code = f"{secrets.randbelow(1_000_000):06d}"
    now = datetime.datetime.utcnow()
    expires = now + datetime.timedelta(minutes=PAIRING_CODE_TTL_MINUTES)
    conn.execute(
        "INSERT INTO pairing_codes (code, created_at, expires_at, assigned_role, assigned_turno, created_by, assigned_display_name) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (code, now.isoformat(), expires.isoformat(), role, turno or None, created_by, display_name),
    )
    conn.commit()
    return {"code": code, "expires_at": expires.isoformat(), "role": role, "turno": turno}


def cancel_pairing_code(code: str):
    """El Director se arrepiente / se equivocó de rol -- invalida el código
    generado antes de que nadie lo use (marcarlo 'usado' es más simple que
    borrarlo, y deja rastro de que fue cancelado, no completado)."""
    conn = db.get_connection()
    conn.execute(
        "UPDATE pairing_codes SET used_at = ? WHERE code = ? AND used_at IS NULL",
        (_now_iso(), code),
    )
    conn.commit()


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


def _validate_persona(display_name: str, dni: str, fecha_nacimiento: str, sexo: str, pin: str):
    if not display_name or not display_name.strip():
        raise PairingError("Falta el nombre completo.")
    if not DNI_PATTERN.match(dni or ""):
        raise PairingError("El DNI tiene que ser numérico, entre 6 y 9 dígitos.")
    try:
        fecha = datetime.date.fromisoformat(fecha_nacimiento)
    except (TypeError, ValueError):
        raise PairingError("Fecha de nacimiento inválida.")
    if fecha > datetime.date.today() or fecha.year < 1900:
        raise PairingError("Fecha de nacimiento fuera de rango.")
    if sexo not in ("F", "M", "X", "NC"):
        raise PairingError("Sexo inválido.")
    try:
        pin_module.validate_pin_format(pin)
    except pin_module.InvalidPinError as e:
        raise PairingError(str(e))


def complete_pairing(
    code: str,
    device_name: str,
    *,
    display_name: str,
    dni: str,
    fecha_nacimiento: str,
    sexo: str,
    pin: str,
) -> dict:
    """Valida el código (sin usar, no vencido, sin demasiados intentos fallidos
    recientes), CREA la identidad real de la persona en `users` (con el rol y
    turno que el Director ya había fijado al generar el código) y emite un
    token nuevo de dispositivo. El token se devuelve UNA sola vez acá — el
    llamador es responsable de guardarlo de forma segura (safeStorage en el
    cliente Electron); el server nunca lo vuelve a mostrar, solo puede
    verificar su hash a futuro.
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

    # Validar los datos personales DESPUÉS de confirmar que el código en sí es
    # válido -- así un código vencido/usado da ese error específico primero,
    # en vez de un error de DNI que confundiría sobre cuál es el problema real.
    _validate_persona(display_name, dni, fecha_nacimiento, sexo, pin)

    user_id = _generate_user_id(display_name)
    token = secrets.token_urlsafe(32)
    device_id = secrets.token_hex(8)
    now = _now_iso()
    pin_hash = pin_module.hash_pin(pin)

    conn.execute(
        "INSERT INTO users (user_id, display_name, role, dni, fecha_nacimiento, sexo, turno, pin_hash, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (user_id, display_name.strip(), row["assigned_role"], dni, fecha_nacimiento, sexo,
         row["assigned_turno"], pin_hash, now, row["created_by"]),
    )
    conn.execute(
        "INSERT INTO devices (device_id, device_name, user_id, token_hash, paired_at) "
        "VALUES (?, ?, ?, ?, ?)",
        (device_id, device_name, user_id, _hash_token(token), now),
    )
    conn.execute("UPDATE pairing_codes SET used_at = ? WHERE code = ?", (now, code))
    conn.commit()
    _record_attempt(code, success=True)

    return {
        "device_id": device_id, "token": token, "user_id": user_id,
        "display_name": display_name.strip(), "role": row["assigned_role"], "turno": row["assigned_turno"],
    }


def verify_token(token: str):
    """Devuelve la fila de `devices` si el token es válido, el dispositivo no
    está revocado Y la persona dueña tampoco lo está, si no None. Revocar una
    PERSONA (no solo un dispositivo puntual) tiene que cortar el acceso de
    inmediato, aunque alguien haya olvidado revocar el device_id específico.
    Comparación con hmac.compare_digest (tiempo constante) por defensa en
    profundidad, aunque el token ya viaja dentro de la red cifrada de Tailscale."""
    if not token:
        return None
    conn = db.get_connection()
    token_hash = _hash_token(token)
    query = (
        "SELECT devices.* FROM devices JOIN users ON devices.user_id = users.user_id "
        "WHERE devices.revoked_at IS NULL AND users.revoked_at IS NULL"
    )
    for row in conn.execute(query):
        if hmac.compare_digest(row["token_hash"], token_hash):
            return row
    return None


def verify_pin_for_device(device_id: str, pin: str):
    """Para el selector local '¿Quién sos?' -- confirma que el PIN corresponde
    a la persona dueña de ESE device_id específico (no alcanza con que el PIN
    exista para cualquier persona). No es autenticación de red -- el
    dispositivo ya está autorizado por su token; esto solo identifica quién
    de las personas vinculadas a esa PC lo está usando ahora mismo."""
    conn = db.get_connection()
    row = conn.execute(
        "SELECT users.pin_hash FROM devices JOIN users ON devices.user_id = users.user_id "
        "WHERE devices.device_id = ? AND devices.revoked_at IS NULL AND users.revoked_at IS NULL",
        (device_id,),
    ).fetchone()
    if row is None:
        return False
    return pin_module.verify_pin(pin, row["pin_hash"])


def list_devices():
    conn = db.get_connection()
    return [dict(r) for r in conn.execute(
        "SELECT device_id, device_name, user_id, paired_at, revoked_at FROM devices ORDER BY paired_at DESC"
    ).fetchall()]


def get_role(user_id: str):
    """Rol real de la persona (no del dispositivo) -- para que server.py pueda
    bloquear rutas por rol (ej. Abate es Enfermero-only, ni Operativa ni un
    dispositivo con token válido pero de otro rol puede escribir ahí)."""
    conn = db.get_connection()
    row = conn.execute("SELECT role FROM users WHERE user_id = ?", (user_id,)).fetchone()
    return row["role"] if row else None


def revoke_device(device_id: str):
    conn = db.get_connection()
    conn.execute(
        "UPDATE devices SET revoked_at = ? WHERE device_id = ? AND revoked_at IS NULL",
        (_now_iso(), device_id),
    )
    conn.commit()


def list_users():
    """Personas ya vinculadas (con identidad real creada), para la pantalla
    del Director 'Personas con acceso a NicOS'. Excluye 'director' -- Nicolás
    no se lista a sí mismo. No devuelve pin_hash (nunca sale de esta función
    hacia server.py -- ver verify_pin_for_device, que sí lo usa pero
    internamente, sin exponerlo)."""
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT users.user_id, users.display_name, users.role, users.dni, users.fecha_nacimiento, "
        "users.sexo, users.turno, users.created_at, users.revoked_at, "
        "devices.device_id, devices.device_name, devices.paired_at, devices.revoked_at as device_revoked_at "
        "FROM users LEFT JOIN devices ON devices.user_id = users.user_id "
        "WHERE users.role != 'director' "
        "ORDER BY users.created_at DESC"
    ).fetchall()
    return [dict(r) for r in rows]


def list_pending_codes():
    """Códigos generados por el Director que todavía nadie usó (ni vencieron
    del todo desde la última limpieza) -- para mostrar 'Código generado,
    pendiente de vincular' en la misma pantalla que list_users()."""
    conn = db.get_connection()
    now = datetime.datetime.utcnow().isoformat()
    rows = conn.execute(
        "SELECT code, assigned_role, assigned_turno, assigned_display_name, created_at, expires_at, created_by "
        "FROM pairing_codes WHERE used_at IS NULL AND expires_at > ? ORDER BY created_at DESC",
        (now,),
    ).fetchall()
    return [dict(r) for r in rows]


def revoke_user(user_id: str):
    """Revoca a la PERSONA (no solo un dispositivo): bloquea su/s token/s de
    inmediato (ver verify_token) y la marca revocada en `users`, para que la
    pantalla del Director la muestre como tal aunque no recuerde cuál era su
    device_id."""
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "UPDATE users SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (now, user_id),
    )
    conn.execute(
        "UPDATE devices SET revoked_at = ? WHERE user_id = ? AND revoked_at IS NULL",
        (now, user_id),
    )
    conn.commit()
