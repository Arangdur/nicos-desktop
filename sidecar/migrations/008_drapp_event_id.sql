-- v0.2.6 -- prepara recordatorios_turnos para la sincronización automática
-- desde la API de DrApp (ver plan, Fase A). Nullable a propósito: los
-- turnos cargados a mano por el formulario del Director (que se mantiene
-- como respaldo) nunca tienen un drapp_event_id.

ALTER TABLE recordatorios_turnos ADD COLUMN drapp_event_id TEXT;

-- Único solo cuando no es NULL (índice parcial) -- así el upsert de la
-- sincronización automática puede detectar "este turno ya se importó antes"
-- sin que los turnos cargados a mano (drapp_event_id = NULL, muchos) choquen
-- entre sí contra una constraint UNIQUE convencional.
CREATE UNIQUE INDEX IF NOT EXISTS idx_recordatorios_drapp_event_id
  ON recordatorios_turnos(drapp_event_id)
  WHERE drapp_event_id IS NOT NULL;
