-- v0.2.6 -- Nota de Crédito C (tipo_cbte 13), para corregir/anular una
-- factura ya emitida. Mismo circuito que una factura (WhatsApp -> IA arma
-- borrador -> Director aprueba -> CAE real), con la diferencia de que ARCA
-- exige que venga referenciada a la factura original ("comprobante
-- asociado") -- eso es `factura_asociada_id`, que Nicolás elige a mano
-- antes de aprobar (la IA no decide sola a qué factura corresponde, mismo
-- principio de no confiarle a la IA un dato fiscal que no puede verificar).

ALTER TABLE facturas_whatsapp ADD COLUMN es_nota_credito INTEGER NOT NULL DEFAULT 0;
ALTER TABLE facturas_whatsapp ADD COLUMN factura_asociada_id TEXT REFERENCES facturas_whatsapp(id);
