-- v0.2.8 (24/08) -- reprogramar un turno por WhatsApp (antes solo se podía
-- cancelar y pedir uno nuevo aparte, dos pasos separados). Reusa el mismo
-- motor de conversación -- solo hace falta un tipo más.
--
-- SQLite no permite ALTER TABLE para ampliar un CHECK -- se recrea la
-- tabla completa, preservando todas las columnas y filas reales ya
-- cargadas (mismo patrón que 018).

CREATE TABLE turnos_conversacion_nueva (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'turno_nuevo' CHECK(tipo IN ('turno_nuevo', 'cancelacion', 'reprogramacion')),
  estado TEXT NOT NULL DEFAULT 'esperando_eleccion'
    CHECK(estado IN ('esperando_especialidad', 'esperando_eleccion', 'esperando_identificacion', 'confirmado', 'cancelado', 'expirado', 'derivado')),
  especialidad TEXT CHECK(especialidad IN ('medicina_general', 'psiquiatria') OR especialidad IS NULL),
  opciones_json TEXT,                 -- [{"day":...,"time":...}, ...] -- NULL para conversaciones de cancelación
  eleccion_index INTEGER,             -- qué opción eligió, para retomarla después de pedir identificación
  consumer_id TEXT,                   -- "consumers/xxx" en DrApp, una vez identificado el paciente
  drapp_event_id TEXT,                -- reprogramación: el turno VIEJO a mover, desde que se ofrecen los horarios nuevos. Turno nuevo/cancelación: el turno real ya creado/cancelado, recién al confirmar.
  mensaje_origen_id TEXT,             -- qué mensaje originó esta conversación (ver cerrar_conversacion_activa)
  creado_at TEXT NOT NULL,
  actualizado_at TEXT NOT NULL
);

INSERT INTO turnos_conversacion_nueva
  (id, telefono, tipo, estado, especialidad, opciones_json, eleccion_index, consumer_id, drapp_event_id, mensaje_origen_id, creado_at, actualizado_at)
  SELECT id, telefono, tipo, estado, especialidad, opciones_json, eleccion_index, consumer_id, drapp_event_id, mensaje_origen_id, creado_at, actualizado_at
  FROM turnos_conversacion;

DROP TABLE turnos_conversacion;
ALTER TABLE turnos_conversacion_nueva RENAME TO turnos_conversacion;

CREATE INDEX IF NOT EXISTS idx_turnos_conversacion_telefono_estado
  ON turnos_conversacion(telefono, estado);
