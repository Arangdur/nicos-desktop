-- v0.2.2 — modelo de roles extensible (Director/Operativa/Enfermero/futuros),
-- con datos personales (DNI, fecha de nacimiento, sexo) y PIN de sesión para
-- dispositivos compartidos entre varias personas (ej. la PC de enfermería de
-- Abate, con continuidad de turno entre Carlos y María).
--
-- SQLite no permite modificar un CHECK existente con ALTER TABLE, así que se
-- recrea `users` -- pero a diferencia de `execution_attempts` en la migración
-- 003 (que nadie referenciaba), `devices.user_id` y `tasks.submitted_by`
-- SÍ referencian `users(user_id)` con FOREIGN KEY. Validado empíricamente
-- (sesión 20/7/2026) que con `PRAGMA foreign_keys = ON` (el modo normal de
-- este proceso, ver db.py) un `DROP TABLE users` falla con
-- "FOREIGN KEY constraint failed" mientras existan esas referencias -- hace
-- falta desactivar el chequeo de FK solo durante esta recreación puntual y
-- reactivarlo antes de terminar (patrón oficial de sqlite.org para recrear
-- una tabla que es padre de FKs de otras).
PRAGMA foreign_keys = OFF;

CREATE TABLE users_new (
  user_id TEXT PRIMARY KEY,
  display_name TEXT NOT NULL,
  -- Antes: CHECK(role IN ('director','operativa')). Ahora es un catálogo
  -- abierto -- la validación de qué roles existen y qué puede hacer cada uno
  -- vive en código (server.py/policies), no en el esquema, a propósito: agregar
  -- un rol nuevo el día de mañana no debe requerir otra migración de esquema.
  role TEXT NOT NULL,
  -- Datos personales: los completa la PERSONA misma en su alta (no el Director
  -- al generar el código) -- por eso son NULL-ables, quedan pendientes hasta
  -- que esa persona vincule su propio dispositivo.
  dni TEXT,
  fecha_nacimiento TEXT,          -- ISO 'YYYY-MM-DD'
  sexo TEXT CHECK(sexo IN ('F','M','X','NC') OR sexo IS NULL),
  turno TEXT,                     -- solo relevante para roles con turno (ej. enfermero); informativo
  -- PIN de 4 dígitos para el selector "¿Quién sos?" en un dispositivo
  -- compartido -- NO es el mecanismo de seguridad real (ese sigue siendo el
  -- token de dispositivo de pairing.py, por Tailscale) -- ver pin.py.
  pin_hash TEXT,
  created_at TEXT NOT NULL,
  created_by TEXT,                -- user_id del Director que asignó el rol/código (auditoría)
  revoked_at TEXT
);

INSERT INTO users_new (user_id, display_name, role, created_at)
  SELECT user_id, display_name, role, datetime('now') FROM users;

DROP TABLE users;
ALTER TABLE users_new RENAME TO users;

PRAGMA foreign_keys = ON;

CREATE INDEX IF NOT EXISTS idx_users_role ON users(role);

-- El código de vinculación ahora lleva el ROL (y turno, si aplica) que el
-- Director eligió ANTES de generarlo -- la persona que lo usa nunca elige su
-- propio rol, solo completa sus datos personales (ver pairing.py.start_pairing).
-- default 'operativa' por compatibilidad con cualquier fila vieja teórica
-- (en la práctica esta tabla es efímera -- los códigos vencen a los 5 min).
ALTER TABLE pairing_codes ADD COLUMN assigned_role TEXT NOT NULL DEFAULT 'operativa';
ALTER TABLE pairing_codes ADD COLUMN assigned_turno TEXT;
ALTER TABLE pairing_codes ADD COLUMN created_by TEXT;
-- Nombre que el Director tipeó al generar el código -- solo una etiqueta para
-- que la vea en su lista ANTES de que la persona complete su alta (ver
-- pairing.list_pending_codes). El registro con autoridad real es el que la
-- persona tipea sobre sí misma en pairing.complete_pairing -- pueden diferir.
ALTER TABLE pairing_codes ADD COLUMN assigned_display_name TEXT;
