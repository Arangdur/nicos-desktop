-- v0.2.6 -- Fase C: fallback de identificación cuando el teléfono no
-- matchea ningún paciente en DrApp -- pedido real de Nicolás (21/08):
-- buscar solo por teléfono no alcanza (un paciente puede escribir desde
-- un número que no es el que tiene cargado). Se agrega un paso más de
-- conversación: pedirle DNI o nombre completo, y solo seguir adelante si
-- eso matchea EXACTAMENTE UN paciente en DrApp -- nunca se adivina entre
-- dos personas con nombre parecido.
--
-- SQLite no permite ALTER TABLE para ampliar un CHECK -- se recrea la
-- tabla completa (013 nunca llegó a producción todavía, no hay filas
-- reales que perder).

CREATE TABLE turnos_conversacion_nueva (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,
  tipo TEXT NOT NULL DEFAULT 'turno_nuevo' CHECK(tipo IN ('turno_nuevo', 'cancelacion')),
  estado TEXT NOT NULL DEFAULT 'esperando_eleccion'
    CHECK(estado IN ('esperando_eleccion', 'esperando_identificacion', 'confirmado', 'cancelado', 'expirado', 'derivado')),
  opciones_json TEXT,                 -- [{"day":...,"time":...}, ...] -- NULL para conversaciones de cancelación
  eleccion_index INTEGER,             -- qué opción eligió, para retomarla después de pedir identificación
  consumer_id TEXT,                   -- "consumers/xxx" en DrApp, una vez identificado el paciente
  drapp_event_id TEXT,                -- turno real ya creado/cancelado en DrApp
  creado_at TEXT NOT NULL,
  actualizado_at TEXT NOT NULL
);

INSERT INTO turnos_conversacion_nueva (id, telefono, estado, opciones_json, consumer_id, drapp_event_id, creado_at, actualizado_at)
  SELECT id, telefono, estado, opciones_json, consumer_id, drapp_event_id, creado_at, actualizado_at FROM turnos_conversacion;

DROP TABLE turnos_conversacion;
ALTER TABLE turnos_conversacion_nueva RENAME TO turnos_conversacion;

CREATE INDEX IF NOT EXISTS idx_turnos_conversacion_telefono_estado
  ON turnos_conversacion(telefono, estado);
