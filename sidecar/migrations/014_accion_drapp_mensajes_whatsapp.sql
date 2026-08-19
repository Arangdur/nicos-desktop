-- v0.2.6 -- Fase C: distingue, en la Bandeja de WhatsApp, un mensaje que
-- todavía no hizo nada en DrApp (una oferta de horarios, o un borrador
-- común) de uno que YA ejecutó una acción real (creó o canceló un turno)
-- antes de que nadie lo apruebe -- ver turnos_conversacion.py. Sin esto,
-- Marianela no tenía forma de saber, mirando la tarjeta, si rechazar un
-- mensaje "solo descarta un borrador" o "el turno ya existe en DrApp
-- igual, esto solo avisa". Nullable a propósito: la enorme mayoría de los
-- mensajes (consultas, recetas, etc.) nunca tocan DrApp.

ALTER TABLE mensajes_whatsapp_entrantes ADD COLUMN accion_drapp TEXT;
