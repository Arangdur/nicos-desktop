-- Usuario técnico para acciones que el sistema ejecuta SOLO -- sin que un
-- Director/Operativa las apruebe. Hasta ahora `resuelto_by`/`creado_by`
-- siempre era la persona real que aprobó algo; el auto-envío de saludos
-- puros (v0.2.6, ver mensajes_whatsapp.generar_borrador) es la primera
-- excepción real a la regla de oro "nada sale sin aprobación humana" --
-- necesita su propia identidad en la auditoría, no pedirle prestado el
-- user_id a Nicolás (mismo error que ya causó el bug de
-- recordatorios.sincronizar_desde_drapp con 'drapp-sync').
INSERT INTO users (user_id, display_name, role, created_at)
VALUES ('sistema', 'Sistema (automático)', 'sistema', datetime('now'));
