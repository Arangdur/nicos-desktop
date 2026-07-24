-- v0.2.4 -- recordatorio de turnos de Medicina General por WhatsApp. Los de
-- Psiquiatría quedan afuera a propósito porque DrApp ya los cubre con su
-- propio recordatorio nativo (pago aparte, ver NICOS_MASTER_SCOPE.md /
-- memoria de sesión). No duplica la agenda de DrApp -- es el registro propio
-- de NicOS de qué recordatorio corresponde y qué pasó con cada uno, lo
-- mínimo necesario para no mandar un mensaje dos veces y para que Marianela
-- vea qué necesita su atención manual.
--
-- La carga de turnos (`importar_turnos`) es manual por ahora -- fuente de
-- datos deliberadamente reemplazable el día que Cormos confirme el acceso a
-- su API real (ver pendiente en memoria del bot de WhatsApp).

CREATE TABLE IF NOT EXISTS recordatorios_turnos (
  id TEXT PRIMARY KEY,
  paciente_nombre TEXT NOT NULL,
  telefono TEXT,                    -- NULL si todavía no se conoce -- entra directo en estado 'sin_telefono'
  fecha_turno TEXT NOT NULL,        -- "YYYY-MM-DD"
  hora_turno TEXT NOT NULL,         -- "HH:MM"
  cobertura TEXT NOT NULL DEFAULT '',
  practica TEXT NOT NULL DEFAULT '',
  estado TEXT NOT NULL DEFAULT 'pendiente'
    CHECK(estado IN ('pendiente', 'enviado', 'sin_telefono', 'fallo_envio')),
  enviado_at TEXT,
  creado_at TEXT NOT NULL,
  creado_by TEXT NOT NULL REFERENCES users(user_id)
);

-- El scheduler recorre 'pendiente' buscando turnos dentro de la ventana de
-- ~24hs -- este índice es lo que consulta en cada tick.
CREATE INDEX IF NOT EXISTS idx_recordatorios_estado_fecha
  ON recordatorios_turnos(estado, fecha_turno, hora_turno);
