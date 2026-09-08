-- v0.2.15 (08/09) -- pedido real de Nicolás: muchas tareas que carga
-- Marianela no son un gasto/ingreso a extraer -- son pedidos operativos
-- ("necesito bono pap de tal paciente") que necesitan una respuesta suya en
-- texto libre, no una clasificación CFO/Abate. Columnas nuevas, sin cambiar
-- el estado de la tarea (una respuesta no "resuelve" nada del flujo de
-- aprobación, es aparte) -- ver tasks.add_director_reply.
ALTER TABLE tasks ADD COLUMN director_reply TEXT;
ALTER TABLE tasks ADD COLUMN director_reply_at TEXT;
