-- Hallazgo real (20/08): rechazar UN mensaje cerraba la conversación de
-- turno activa del teléfono entero, sin importar si ese mensaje era el que
-- de verdad la había creado. Un paciente respondió "1" a una oferta ya
-- aprobada y enviada -- pero un mensaje DUPLICADO posterior (mismo
-- teléfono, ya con la conversación abierta) se rechazó por separado, y esa
-- rechazo cerró la conversación real -- el "1" nunca se pudo interpretar.
-- Guarda qué mensaje originó cada conversación para que
-- turnos_conversacion.cerrar_conversacion_activa solo la cierre si el
-- mensaje rechazado es efectivamente el que la creó.
ALTER TABLE turnos_conversacion ADD COLUMN mensaje_origen_id TEXT;
