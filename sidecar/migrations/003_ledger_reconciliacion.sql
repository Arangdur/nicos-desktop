-- v0.2.1-rc1 — reconciliación durable del efecto externo, reclamo atómico del
-- worker, trazabilidad de política por tarea.
--
-- SQLite no permite modificar un CHECK existente con ALTER TABLE -- se recrea
-- execution_attempts. Todavía no hay datos productivos reales en esta tabla
-- (solo de pruebas), así que el mapeo de valores viejos a los nuevos es directo:
-- running -> effect_started, completed -> effect_confirmed, failed -> effect_failed.
ALTER TABLE execution_attempts RENAME TO execution_attempts_old;

CREATE TABLE execution_attempts (
  execution_id TEXT PRIMARY KEY,
  task_id TEXT NOT NULL REFERENCES tasks(task_id),
  operation_id TEXT NOT NULL UNIQUE,
  started_at TEXT NOT NULL,
  finished_at TEXT,
  -- claimed: fila creada, subprocess todavía no invocado -- certeza total de
  --   que no hubo efecto externo si el proceso muere acá.
  -- effect_started: subprocess ya invocado -- acá vive la incertidumbre real.
  -- effect_confirmed / effect_failed: resultado conocido (directo o por
  --   reconciliación contra el ledger de registrar_movimiento.py tras un reinicio).
  -- uncertain: la reconciliación misma no pudo determinarlo (ej. ledger illegible).
  status TEXT NOT NULL CHECK(status IN ('claimed','effect_started','effect_confirmed','effect_failed','uncertain')),
  executor TEXT NOT NULL,
  result_json TEXT,
  error_message TEXT
);

INSERT INTO execution_attempts (execution_id, task_id, operation_id, started_at, finished_at, status, executor, result_json, error_message)
SELECT execution_id, task_id, operation_id, started_at, finished_at,
       CASE status
         WHEN 'running' THEN 'effect_started'
         WHEN 'completed' THEN 'effect_confirmed'
         WHEN 'failed' THEN 'effect_failed'
         ELSE status
       END,
       executor, result_json, error_message
FROM execution_attempts_old;

DROP TABLE execution_attempts_old;
CREATE INDEX IF NOT EXISTS idx_execution_attempts_task_id ON execution_attempts(task_id);

-- Reclamo atómico del worker: evita que dos procesos ejecuten la misma tarea
-- si por error hay dos sidecars corriendo contra la misma base (ver worker.py).
ALTER TABLE tasks ADD COLUMN locked_by TEXT;
ALTER TABLE tasks ADD COLUMN locked_at TEXT;

-- Trazabilidad de política: qué versión/hash de policies/risk_policy.yaml se
-- usó para clasificar esta tarea específica (ver centro_mando_adapter.py).
ALTER TABLE tasks ADD COLUMN policy_version TEXT;
ALTER TABLE tasks ADD COLUMN policy_hash TEXT;
