"""
Modelo de roles extensible (v0.2.2): `users` deja de ser una tabla semilla
fija de 2 filas ('nicolas'/'marianela') y pasa a ser un catálogo abierto que
crece con cada alta real -- el Director asigna rol (y turno) AL GENERAR el
código, la persona completa sus datos personales (DNI, fecha de nacimiento,
sexo) y un PIN al vincularse. Este archivo prueba, con la base real (no
mocks), que:

1. La migración 005 corre limpio sobre la base real 001-004 (FKs intactas).
2. El código de vinculación lleva el rol/turno que el Director eligió --
   la persona que lo usa no puede elegir el suyo propio.
3. complete_pairing() crea una fila completa en `users` (todos los campos
   personales), no solo un token.
4. El PIN nunca se guarda en texto plano, ni sale expuesto en list_users().
5. Revocar a una PERSONA bloquea su próximo login de inmediato (verify_token).
6. `sexo` respeta el CHECK; insertar rol 'enfermero' funciona sin tocar el
   CHECK viejo (era imposible antes de esta migración).
7. Dos personas con el mismo nombre de pila no colisionan de user_id.
8. Un código pendiente aparece en list_pending_codes() y desaparece al
   completarse (o al cancelarse).

Uso: python3 sidecar/tests/test_roles_extensibles.py
"""
import datetime
import os
import sqlite3
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("db", "pairing", "pin"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import pairing as pairing_mod
    import pin as pin_mod
    return db_mod, pairing_mod, pin_mod


PERSONA_BASE = dict(
    display_name="Carlos Fernández", dni="30123456",
    fecha_nacimiento="1990-08-12", sexo="M", pin="1234",
)


class TestRolesExtensibles(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.pairing, self.pin = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    # 1. Migración limpia + FKs intactas -----------------------------------

    def test_migracion_preserva_integridad_referencial(self):
        conn = self.db.get_connection()
        self.assertEqual(list(conn.execute("PRAGMA foreign_key_check")), [])
        self.assertEqual(conn.execute("PRAGMA integrity_check").fetchone()[0], "ok")
        # users semilla (nicolas/marianela) sobrevivieron la recreación de tabla
        roles = {r["user_id"]: r["role"] for r in conn.execute("SELECT user_id, role FROM users")}
        self.assertEqual(roles.get("nicolas"), "director")
        self.assertEqual(roles.get("marianela"), "operativa")

    # 2. El código lleva el rol/turno elegido por el Director ---------------

    def test_codigo_lleva_rol_asignado_por_director(self):
        result = self.pairing.start_pairing("enfermero", turno="noche", created_by="nicolas", display_name="María")
        self.assertEqual(result["role"], "enfermero")
        self.assertEqual(result["turno"], "noche")

        pairing_result = self.pairing.complete_pairing(
            result["code"], "PC de enfermería", **PERSONA_BASE,
        )
        # El rol final es el que el Director asignó -- la persona nunca lo eligió.
        self.assertEqual(pairing_result["role"], "enfermero")
        self.assertEqual(pairing_result["turno"], "noche")

    def test_rol_no_asignable_por_codigo_es_rechazado(self):
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.start_pairing("director")

    # 3. Alta crea una fila COMPLETA en users, no solo un token -------------

    def test_alta_crea_ficha_completa(self):
        code = self.pairing.start_pairing("operativa", created_by="nicolas")["code"]
        result = self.pairing.complete_pairing(code, "PC de Daniela", **PERSONA_BASE)

        conn = self.db.get_connection()
        row = conn.execute("SELECT * FROM users WHERE user_id = ?", (result["user_id"],)).fetchone()
        self.assertIsNotNone(row)
        self.assertEqual(row["dni"], "30123456")
        self.assertEqual(row["fecha_nacimiento"], "1990-08-12")
        self.assertEqual(row["sexo"], "M")
        self.assertEqual(row["display_name"], "Carlos Fernández")
        self.assertIsNotNone(row["created_at"])

    def test_dni_invalido_es_rechazado(self):
        code = self.pairing.start_pairing("operativa")["code"]
        persona = {**PERSONA_BASE, "dni": "abc"}
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.complete_pairing(code, "PC", **persona)

    def test_fecha_nacimiento_futura_es_rechazada(self):
        code = self.pairing.start_pairing("operativa")["code"]
        futuro = (datetime.date.today() + datetime.timedelta(days=1)).isoformat()
        persona = {**PERSONA_BASE, "fecha_nacimiento": futuro}
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.complete_pairing(code, "PC", **persona)

    # 4. El PIN nunca queda en texto plano -----------------------------------

    def test_pin_se_guarda_hasheado_no_expuesto(self):
        code = self.pairing.start_pairing("operativa")["code"]
        result = self.pairing.complete_pairing(code, "PC", **PERSONA_BASE)

        conn = self.db.get_connection()
        row = conn.execute("SELECT pin_hash FROM users WHERE user_id = ?", (result["user_id"],)).fetchone()
        self.assertNotEqual(row["pin_hash"], "1234")
        self.assertEqual(row["pin_hash"], self.pin.hash_pin("1234"))

        # list_users() (lo que ve el Director) nunca debe incluir pin_hash
        personas = self.pairing.list_users()
        self.assertTrue(all("pin_hash" not in p for p in personas))

    def test_pin_formato_invalido_rechazado(self):
        code = self.pairing.start_pairing("operativa")["code"]
        persona = {**PERSONA_BASE, "pin": "12"}
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.complete_pairing(code, "PC", **persona)

    def test_verify_pin_for_device(self):
        code = self.pairing.start_pairing("operativa")["code"]
        result = self.pairing.complete_pairing(code, "PC", **PERSONA_BASE)
        self.assertTrue(self.pairing.verify_pin_for_device(result["device_id"], "1234"))
        self.assertFalse(self.pairing.verify_pin_for_device(result["device_id"], "9999"))

    # 5. Revocar a una persona bloquea su próximo login de inmediato --------

    def test_revoke_user_bloquea_token_de_inmediato(self):
        code = self.pairing.start_pairing("operativa")["code"]
        result = self.pairing.complete_pairing(code, "PC", **PERSONA_BASE)
        self.assertIsNotNone(self.pairing.verify_token(result["token"]))

        self.pairing.revoke_user(result["user_id"])

        self.assertIsNone(self.pairing.verify_token(result["token"]))
        conn = self.db.get_connection()
        user_row = conn.execute("SELECT revoked_at FROM users WHERE user_id = ?", (result["user_id"],)).fetchone()
        self.assertIsNotNone(user_row["revoked_at"])
        device_row = conn.execute("SELECT revoked_at FROM devices WHERE device_id = ?", (result["device_id"],)).fetchone()
        self.assertIsNotNone(device_row["revoked_at"])

    # 6. Catálogo abierto de roles + CHECK de sexo ---------------------------

    def test_sexo_invalido_rechazado_por_check(self):
        conn = self.db.get_connection()
        with self.assertRaises(sqlite3.IntegrityError):
            conn.execute(
                "INSERT INTO users (user_id, display_name, role, sexo, created_at) VALUES (?,?,?,?,datetime('now'))",
                ("test-bad-sexo", "Test", "enfermero", "ZZ"),
            )

    def test_rol_enfermero_funciona_sin_check_viejo(self):
        # Antes de la migración 005 esto era literalmente imposible (CHECK
        # limitaba role a 'director'/'operativa') -- confirma que el catálogo
        # es de verdad abierto, no solo que agregamos un tercer valor fijo.
        code = self.pairing.start_pairing("enfermero", turno="manana")["code"]
        result = self.pairing.complete_pairing(code, "PC enfermería", **PERSONA_BASE)
        conn = self.db.get_connection()
        row = conn.execute("SELECT role FROM users WHERE user_id = ?", (result["user_id"],)).fetchone()
        self.assertEqual(row["role"], "enfermero")

    # 7. Nombres repetidos no colisionan de user_id --------------------------

    def test_nombres_iguales_generan_user_id_distintos(self):
        code1 = self.pairing.start_pairing("enfermero", turno="manana")["code"]
        r1 = self.pairing.complete_pairing(code1, "PC 1", **PERSONA_BASE)

        code2 = self.pairing.start_pairing("enfermero", turno="noche")["code"]
        persona2 = {**PERSONA_BASE, "dni": "30999888"}
        r2 = self.pairing.complete_pairing(code2, "PC 2", **persona2)

        self.assertNotEqual(r1["user_id"], r2["user_id"])

    # 8. Códigos pendientes: aparecen y desaparecen correctamente -----------

    def test_codigo_pendiente_aparece_y_desaparece_al_completarse(self):
        result = self.pairing.start_pairing("operativa", created_by="nicolas", display_name="Daniela")
        pendientes = self.pairing.list_pending_codes()
        self.assertTrue(any(p["code"] == result["code"] for p in pendientes))

        self.pairing.complete_pairing(result["code"], "PC", **PERSONA_BASE)
        pendientes_despues = self.pairing.list_pending_codes()
        self.assertFalse(any(p["code"] == result["code"] for p in pendientes_despues))

    def test_cancelar_codigo_pendiente(self):
        result = self.pairing.start_pairing("operativa", display_name="Alguien")
        self.pairing.cancel_pairing_code(result["code"])
        pendientes = self.pairing.list_pending_codes()
        self.assertFalse(any(p["code"] == result["code"] for p in pendientes))
        # Y ya no se puede completar -- quedó marcado como usado.
        with self.assertRaises(self.pairing.PairingError):
            self.pairing.complete_pairing(result["code"], "PC", **PERSONA_BASE)


if __name__ == "__main__":
    unittest.main()
