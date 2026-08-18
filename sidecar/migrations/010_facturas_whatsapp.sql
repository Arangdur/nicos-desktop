-- v0.2.6 (proyecto en curso, ver CFO y Decisiones Estrategicas/
-- Proyecto_Facturacion_WhatsApp_ARCA.md) -- bot de facturación electrónica
-- (ARCA/WSFE) por WhatsApp, mismo patrón de aprobación humana que
-- mensajes_whatsapp_entrantes: paciente/Nicolás escribe "facturale X a Y" ->
-- la IA arma el borrador de factura (monto/concepto/cliente) ->
-- 'borrador_generado' -> Nicolás aprueba (Director-only, SIEMPRE -- emitir
-- una factura real en ARCA es fiscal, nunca lo puede aprobar Operativa,
-- a diferencia de mensajes_whatsapp donde Operativa sí puede aprobar lo no
-- clínico) -> ahí recién se pide el CAE real y se genera+manda el PDF.
--
-- La IA NUNCA emite sola -- mismo principio no negociable que
-- mensajes_whatsapp_entrantes.

CREATE TABLE IF NOT EXISTS facturas_whatsapp (
  id TEXT PRIMARY KEY,
  telefono TEXT NOT NULL,             -- quién pidió la factura por WhatsApp
  texto_original TEXT NOT NULL,
  monto REAL,
  concepto TEXT,
  cliente_nombre TEXT,                -- null = Consumidor Final
  cliente_doc_tipo INTEGER,           -- 99=Consumidor Final, 96=DNI, 80=CUIT
  cliente_doc_nro INTEGER,
  estado TEXT NOT NULL DEFAULT 'recibido'
    CHECK(estado IN ('recibido', 'borrador_generado', 'error_extraccion', 'emitida', 'rechazada', 'error_emision')),
  error_detalle TEXT,                 -- de extracción o de emisión, lo que haya fallado último
  pto_venta INTEGER,
  tipo_cbte INTEGER,
  numero_cbte INTEGER,
  cae TEXT,
  cae_vencimiento TEXT,
  pdf_path TEXT,                      -- ruta local del PDF ya generado, para servirlo por /facturas/<id>/pdf
  recibido_at TEXT NOT NULL,
  borrador_generado_at TEXT,
  resuelto_at TEXT,
  resuelto_by TEXT REFERENCES users(user_id)
);

CREATE INDEX IF NOT EXISTS idx_facturas_whatsapp_estado
  ON facturas_whatsapp(estado, recibido_at);
