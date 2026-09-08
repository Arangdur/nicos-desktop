-- v0.2.15 (08/09) -- pedido real de Nicolás: poder responderle a Marianela
-- por WhatsApp de verdad, no solo dejar la respuesta en pantalla. Hace falta
-- su teléfono, que hoy no se guarda en ningún lado (el alta solo pide
-- DNI/fecha de nacimiento/sexo/PIN). NULL-able igual que el resto de los
-- datos personales -- el Director lo carga a mano desde Ajustes (ella ya
-- está vinculada hace meses, no va a volver a pasar por el alta).
ALTER TABLE users ADD COLUMN telefono TEXT;
