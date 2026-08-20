-- v0.2.7 (20/08) -- Fase de Psiquiatría por WhatsApp: el consultorio ahora
-- agenda dos especialidades (antes solo Medicina General), así que hace
-- falta (1) saber cuál pide un "quiero un turno" ambiguo -- nuevo estado
-- 'esperando_especialidad', con el menú explícito 1/2/3 que confirmó
-- Nicolás -- y (2) recordar qué especialidad eligió cada conversación,
-- para usar el service key/franja/ubicación correctos al confirmar el
-- turno más adelante (columna `especialidad`).
--
-- SQLite no permite ALTER TABLE para ampliar un CHECK -- se recrea la
-- tabla completa, preservando todas las columnas y filas reales ya
-- cargadas (a diferencia de 015, este módulo ya está en uso real).

CREATE TABLE turnos_conversacion_nueva (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'turno_nuevo' CHECK(tipo IN ('turno_nuevo', 'cancelacion')),
  estado TEXT NOT NULL DEFAULT 'esperando_eleccion'
    CHECK(estado IN ('esperando_especialidad', 'esperando_eleccion', 'esperando_identificacion', 'confirmado', 'cancelado', 'expirado', 'derivado')),
  especialidad TEXT CHECK(especialidad IN ('medicina_general', 'psiquiatria') OR especialidad IS NULL),
  opciones_json TEXT,                 -- [{"day":...,"time":...}, ...] -- NULL para conversaciones de cancelación
  eleccion_index INTEGER,             -- qué opción eligió, para retomarla después de pedir identificación
  consumer_id TEXT,                   -- "consumers/xxx" en DrApp, una vez identificado el paciente
  drapp_event_id TEXT,                -- turno real ya creado/cancelado en DrApp
  mensaje_origen_id TEXT,             -- qué mensaje originó esta conversación (ver cerrar_conversacion_activa)
  creado_at TEXT NOT NULL,
  actualizado_at TEXT NOT NULL
);

-- Las conversaciones viejas ya cargadas eran todas de Medicina General
-- (única especialidad que existía) -- se las marca explícitamente en vez
-- de dejarlas NULL, para que no se confundan con "esperando que elija
-- especialidad" si alguna quedara reabierta por error.
INSERT INTO turnos_conversacion_nueva
  (id, telefono, tipo, estado, especialidad, opciones_json, eleccion_index, consumer_id, drapp_event_id, mensaje_origen_id, creado_at, actualizado_at)
  SELECT id, telefono, tipo, estado, 'medicina_general', opciones_json, eleccion_index, consumer_id, drapp_event_id, mensaje_origen_id, creado_at, actualizado_at
  FROM turnos_conversacion;

DROP TABLE turnos_conversacion;
ALTER TABLE turnos_conversacion_nueva RENAME TO turnos_conversacion;

CREATE INDEX IF NOT EXISTS idx_turnos_conversacion_telefono_estado
  ON turnos_conversacion(telefono, estado);
