"""
v0.2.15 (08/09) -- pedido real de Nicolás: muchas tareas de Marianela no son
un gasto/ingreso a extraer ("necesito bono pap de tal paciente"), necesitan
una respuesta en texto libre -- nunca cambia el estado de la tarea, es un
canal aparte del flujo de aprobación. También cubre pairing.set_telefono
(hace falta el teléfono para poder mandar la respuesta por WhatsApp real).

Uso: python3 sidecar/tests/test_director_reply.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import pairing as pairing_mod
    return db_mod, tasks_mod, pairing_mod


class TestDirectorReply(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.pairing = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def test_responder_no_cambia_el_estado(self):
        result = self.tasks.create_task("reply-1", "marianela", None, "necesito bono pap de fulano")
        task_id = result["task"]["task_id"]
        estado_antes = self.tasks.get_task_dict(task_id)["state"]

        actualizado = self.tasks.add_director_reply(task_id, "nicolas", "  Sí, dale el bono. ")

        self.assertEqual(actualizado["state"], estado_antes)
        self.assertEqual(actualizado["director_reply"], "Sí, dale el bono.")  # recortado
        self.assertIsNotNone(actualizado["director_reply_at"])

    def test_responder_deja_evento_de_auditoria(self):
        result = self.tasks.create_task("reply-2", "marianela", None, "algo")
        task_id = result["task"]["task_id"]
        self.tasks.add_director_reply(task_id, "nicolas", "listo")

        eventos = self.tasks.get_task_events(task_id)
        self.assertTrue(any(e["actor"] == "nicolas" and "director_reply" in (e["detail_json"] or "") for e in eventos))

    def test_responder_tarea_inexistente_falla(self):
        with self.assertRaises(ValueError):
            self.tasks.add_director_reply("no-existe", "nicolas", "algo")

    def test_responder_vacio_falla(self):
        result = self.tasks.create_task("reply-3", "marianela", None, "algo")
        task_id = result["task"]["task_id"]
        with self.assertRaises(ValueError):
            self.tasks.add_director_reply(task_id, "nicolas", "   ")

    def test_set_telefono_persiste_y_aparece_en_list_users(self):
        # marianela-f434 no existe en esta DB fresca -- creamos un usuario real
        # vía pairing para probar el flujo completo, no solo la función suelta.
        codigo = self.pairing.start_pairing("operativa", created_by="nicolas", display_name="Marianela")["code"]
        alta = self.pairing.complete_pairing(
            codigo, "PC de prueba", display_name="Marianela", dni="30111222",
            fecha_nacimiento="1990-01-01", sexo="F", pin="1234",
        )
        user_id = alta["user_id"]

        self.pairing.set_telefono(user_id, "+5493537599192")

        personas = self.pairing.list_users()
        marianela = next(u for u in personas if u["user_id"] == user_id)
        self.assertEqual(marianela["telefono"], "+5493537599192")

    def test_set_telefono_vacio_borra_el_dato(self):
        codigo = self.pairing.start_pairing("operativa", created_by="nicolas", display_name="Marianela")["code"]
        alta = self.pairing.complete_pairing(
            codigo, "PC de prueba", display_name="Marianela", dni="30111222",
            fecha_nacimiento="1990-01-01", sexo="F", pin="1234",
        )
        user_id = alta["user_id"]
        self.pairing.set_telefono(user_id, "+5493537599192")

        self.pairing.set_telefono(user_id, "")

        marianela = next(u for u in self.pairing.list_users() if u["user_id"] == user_id)
        self.assertIsNone(marianela["telefono"])

    def test_set_telefono_usuario_inexistente_falla(self):
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.set_telefono("no-existe", "+549...")


if __name__ == "__main__":
    unittest.main()
