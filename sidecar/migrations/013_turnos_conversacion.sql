-- v0.2.6 -- Fase C: estado de la conversación de reserva/cancelación de
-- turnos por WhatsApp. Una fila por conversación ACTIVA para un teléfono
-- (a lo sumo una a la vez) -- guarda qué se le ofreció al paciente para
-- poder interpretar su respuesta ("el segundo", "el de las 10hs") sin
-- volver a preguntarle nada que ya dijo.
--
-- Separada de mensajes_whatsapp_entrantes a propósito: esa tabla es UN
-- mensaje = UNA fila (con su propio borrador/aprobación); esta tabla vive
-- MIENTRAS DURA una conversación de varios mensajes de ida y vuelta.

CREATE TABLE IF NOT EXISTS turnos_conversacion (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,
  estado TEXT NOT NULL DEFAULT 'esperando_eleccion'
    CHECK(estado IN ('esperando_eleccion', 'confirmado', 'cancelado', 'expirado', 'derivado')),
  opciones_json TEXT NOT NULL,        -- [{"day":"2026-08-20","time":"10:00"}, ...] -- lo que se ofreció, tal cual
  consumer_id TEXT,                   -- "consumers/xxx" en DrApp, una vez identificado el paciente
  drapp_event_id TEXT,                -- turno real ya creado en DrApp, una vez confirmado
  creado_at TEXT NOT NULL,
  actualizado_at TEXT NOT NULL
);

-- El worker busca "¿hay una conversación activa para este teléfono?" en
-- cada mensaje entrante -- este índice es lo que esa consulta usa. Solo
-- puede haber UNA fila 'esperando_eleccion' por teléfono a la vez (lo
-- aplica el código, no una constraint -- SQLite no tiene índice único
-- parcial condicionado a valor de columna de forma simple y portable acá).
CREATE INDEX IF NOT EXISTS idx_turnos_conversacion_telefono_estado
  ON turnos_conversacion(telefono, estado);
