-- v0.2.1 — correcciones críticas post-revisión.
-- task_revision: se incrementa cada vez que la tarea (re)entra a 'classified'.
-- La aprobación exige que revisión Y hash coincidan con los actuales (ver tasks.py).
ALTER TABLE tasks ADD COLUMN task_revision INTEGER NOT NULL DEFAULT 0;

-- Idempotencia del EFECTO EXTERNO (no solo de la fila `tasks`). Cada intento real
-- de ejecutar registrar_movimiento.py deja una fila acá con su operation_id único
-- ANTES de invocar el subprocess — así una tarea que quedó 'executing' al reiniciar
-- se puede diferenciar de una que nunca llegó a intentar ejecutarse.
CREATE TABLE execution_attempts (
  execution_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  operation_id TEXT NOT NULL UNIQUE,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  status TEXT NOT NULL CHECK(status IN ('running','completed','failed')),
  executor TEXT NOT NULL,
  result_json TEXT,
  error_message TEXT
);
CREATE INDEX IF NOT EXISTS idx_execution_attempts_task_id ON execution_attempts(task_id);

-- Rate limiting de pairing: 5+ intentos fallidos en 5 min invalida el código aunque
-- no haya vencido su TTL.
CREATE TABLE pairing_attempts (
  attempt_id INTEGER PRIMARY KEY AUTOINCREMENT,
  code_attempted TEXT NOT NULL,
  success INTEGER NOT NULL,
  created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_pairing_attempts_code ON pairing_attempts(code_attempted);
