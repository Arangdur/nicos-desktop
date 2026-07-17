-- NicOS Core v0.2 — esquema inicial.
-- SQLite, sin ORM. users/devices existen para atribuir auditoría (quién hizo qué),
-- no son un sistema de autenticación completo — el pairing (ver pairing.py) es lo
-- que realmente autoriza a un dispositivo a hablarle al sidecar.

CREATE TABLE IF NOT EXISTS users (
  user_id TEXT PRIMARY KEY,       -- 'nicolas' | 'marianela'
  display_name TEXT NOT NULL,
  role TEXT NOT NULL CHECK(role IN ('director','operativa'))
);

INSERT OR IGNORE INTO users (user_id, display_name, role) VALUES
  ('nicolas', 'Nicolás', 'director'),
  ('marianela', 'Marianela', 'operativa');

CREATE TABLE IF NOT EXISTS pairing_codes (
  code TEXT PRIMARY KEY,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL,
  used_at TEXT
);

CREATE TABLE IF NOT EXISTS devices (
  device_id TEXT PRIMARY KEY,
  device_name TEXT NOT NULL,
  user_id TEXT NOT NULL REFERENCES users(user_id),
  token_hash TEXT NOT NULL UNIQUE,
  paired_at TEXT NOT NULL,
  revoked_at TEXT
);

CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY,
  idempotency_key TEXT NOT NULL UNIQUE,
  submitted_by TEXT NOT NULL REFERENCES users(user_id),
  device_id TEXT REFERENCES devices(device_id),
  raw_text TEXT NOT NULL,
  attachment_path TEXT,
  state TEXT NOT NULL,
  domain TEXT,
  intent TEXT,
  extracted_json TEXT,
  risk_level TEXT,
  action_version_hash TEXT,
  approved_by TEXT,
  approved_at TEXT,
  approved_action_hash TEXT,
  result_json TEXT,
  error_message TEXT,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_tasks_state ON tasks(state);
CREATE INDEX IF NOT EXISTS idx_tasks_submitted_by ON tasks(submitted_by);

CREATE TABLE IF NOT EXISTS task_events (
  event_id INTEGER PRIMARY KEY AUTOINCREMENT,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  from_state TEXT,
  to_state TEXT NOT NULL,
  actor TEXT NOT NULL,             -- 'nicolas' | 'marianela' | 'system' | 'ai'
  detail_json TEXT,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_task_events_task_id ON task_events(task_id);
