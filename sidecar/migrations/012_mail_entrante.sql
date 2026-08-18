-- v0.2.6 -- bandeja de mail entrante (Gmail) para dos casillas: consultorio
-- (novogen.salud@gmail.com) y Fundación Abate (fundacion.abate@gmail.com).
-- Mismo patrón que mensajes_whatsapp_entrantes: llega -> 'recibido' -> el
-- worker le pide a la IA que clasifique y arme un borrador de respuesta ->
-- 'borrador_generado' -> el Director aprueba (editado o tal cual) o rechaza
-- -> si aprueba, ahí recién se manda por Gmail (gmail_client.py).
--
-- Diferencia deliberada con mensajes_whatsapp_entrantes: acá la aprobación
-- es SIEMPRE Director-only, sin la excepción "Operativa puede si no es
-- clínico" -- un mail mal contestado en nombre del consultorio o de la
-- Fundación es tan sensible como una factura (ver facturas.py), no hay
-- equivalente de "esto es rutina, lo puede resolver cualquiera".

CREATE TABLE IF NOT EXISTS mail_entrante (
  id TEXT PRIMARY KEY,
  casilla TEXT NOT NULL CHECK(casilla IN ('consultorio', 'abate')),
  gmail_message_id TEXT,              -- id real de Gmail, para no duplicar si el worker lo vuelve a ver
  remitente TEXT NOT NULL,
  asunto TEXT NOT NULL DEFAULT '',
  cuerpo_original TEXT NOT NULL,
  categoria TEXT,                     -- clasificación que arma la IA (turno/administrativo/medico/queja/spam/otro)
  estado TEXT NOT NULL DEFAULT 'recibido'
    CHECK(estado IN ('recibido', 'borrador_generado', 'error_clasificacion', 'aprobado_enviado', 'rechazado')),
  error_detalle TEXT,                 -- por qué falló la clasificación, si falló
  borrador_respuesta TEXT,
  respuesta_final TEXT,
  recibido_at TEXT NOT NULL,
  borrador_generado_at TEXT,
  resuelto_at TEXT,
  resuelto_by TEXT REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_mail_entrante_estado
  ON mail_entrante(estado, recibido_at);

-- Único por casilla+gmail_message_id (cuando se conoce) -- si el worker
-- vuelve a listar el mismo mail (por ejemplo tras un reinicio), no lo
-- duplica. NULL nunca choca con NULL en SQLite, así que esto no bloquea el
-- caso de pruebas/datos cargados a mano sin id real de Gmail.
CREATE UNIQUE INDEX IF NOT EXISTS idx_mail_entrante_gmail_id
  ON mail_entrante(casilla, gmail_message_id) WHERE gmail_message_id IS NOT NULL;
