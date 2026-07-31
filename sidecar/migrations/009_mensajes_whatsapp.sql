-- v0.2.5 -- bandeja de mensajes de WhatsApp ENTRANTES (Fase B/C del diseño
-- original). Hasta acá, NicOS solo mandaba WhatsApp (recordatorios) -- esto
-- es la primera vez que recibe uno. Un paciente escribe, el mensaje entra acá
-- en 'recibido', el worker le pide a la IA que lo clasifique y arme un
-- borrador de respuesta ('borrador_generado'), y una persona (Director u
-- Operativa, salvo que `requiere_profesional`) lo aprueba -- editado o tal
-- cual -- antes de que salga. Nunca se manda nada sin ese paso: la IA arma el
-- borrador, nunca lo envía sola.
--
-- `requiere_profesional`: la clasificación marca esto en true para cualquier
-- pedido que roce lo clínico (receta, consulta médica puntual) -- ese
-- borrador solo lo puede aprobar el Director, nunca Marianela, mismo
-- criterio ya aplicado a los scopes de DrApp (clinical/prescriptions
-- exigen identidad de profesional).

CREATE TABLE IF NOT EXISTS mensajes_whatsapp_entrantes (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,
  texto_original TEXT NOT NULL,
  clasificacion TEXT
    CHECK(clasificacion IN ('turno_nuevo', 'cancelacion', 'reprogramacion', 'consulta_general', 'receta', 'ambiguo') OR clasificacion IS NULL),
  requiere_profesional INTEGER NOT NULL DEFAULT 0,  -- 0/1 -- SQLite no tiene BOOLEAN real
  urgente INTEGER NOT NULL DEFAULT 0,
  borrador_respuesta TEXT,
  respuesta_final TEXT,               -- lo que realmente se mandó -- puede diferir del borrador si se editó
  estado TEXT NOT NULL DEFAULT 'recibido'
    CHECK(estado IN ('recibido', 'borrador_generado', 'error_clasificacion', 'aprobado_enviado', 'rechazado')),
  error_clasificacion TEXT,           -- detalle si la IA falló, para que la persona vea el mensaje crudo igual
  recibido_at TEXT NOT NULL,
  borrador_generado_at TEXT,
  resuelto_at TEXT,
  resuelto_by TEXT REFERENCES users(user_id)
);

-- El scheduler recorre 'recibido' para generar borradores, y la UI recorre
-- 'borrador_generado' para mostrar la bandeja pendiente -- mismo índice sirve
-- para ambos por estado.
CREATE INDEX IF NOT EXISTS idx_mensajes_whatsapp_estado
  ON mensajes_whatsapp_entrantes(estado, recibido_at);
