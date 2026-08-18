"""
Bandeja de mail entrante -- mismo estilo que test_mensajes_whatsapp.py.
Prueba con evidencia real (HTTP contra servidor is_lan=True con tokens de
verdad) que:

1. La IA (mockeada, nunca red real) clasifica y arma un borrador -- si falla,
   el mail NUNCA se pierde, queda en 'error_clasificacion' con el original
   intacto.
2. Aprobar/rechazar es SIEMPRE Director-only, sin excepción (a diferencia de
   WhatsApp) -- tanto llamando el módulo directo como por HTTP.
3. sincronizar_casilla() no duplica un mail ya visto (dedupe por
   gmail_message_id) y es un no-op silencioso si la casilla no tiene
   credenciales configuradas.
4. Aprobar manda por Gmail (mockeado) -- rechazar nunca manda nada.
5. El glue del worker genera borradores para todo lo que esté 'recibido'.

Usa base de datos temporal -- nunca toca nicos.db real, y nunca pega contra
la API real de Gmail.

Uso: python3 sidecar/tests/test_mail_entrante.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from unittest.mock import patch

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["NICOS_DB_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402
import gmail_client  # noqa: E402
import mail_entrante  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402
import worker  # noqa: E402

db.run_migrations()


def _make_server(is_lan: bool):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.is_lan = is_lan
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _pair(role, display_name):
    code = pairing.start_pairing(role, created_by="nicolas", display_name=display_name)["code"]
    return pairing.complete_pairing(
        code, f"PC de {display_name}",
        display_name=display_name, dni="30" + str(abs(hash(display_name)))[:6],
        fecha_nacimiento="1990-01-01", sexo="NC", pin="1234",
    )


def _reset_db(db_file):
    os.environ["NICOS_DB_PATH"] = db_file
    import importlib
    importlib.reload(db)
    db.run_migrations()


class TestMailHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _reset_db(_TMP_DB.name)
        cls.lan_httpd = _make_server(is_lan=True)
        cls.operativa = _pair("operativa", "Marianela Prueba Mail")

    @classmethod
    def tearDownClass(cls):
        cls.lan_httpd.shutdown()
        os.unlink(_TMP_DB.name)

    @staticmethod
    def _request(httpd, method, path, body=None, token=None):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        headers = {"Content-Type": "application/json"}
        if token:
            headers["Authorization"] = f"Bearer {token}"
        payload = json.dumps(body).encode() if body is not None else b""
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def _as(self, identity, method, path, body=None):
        return self._request(self.lan_httpd, method, path, body, token=identity["token"])

    def test_operativa_no_puede_ver_la_bandeja(self):
        # A diferencia de WhatsApp, mail nunca tuvo a Marianela en el diseño.
        status, data = self._as(self.operativa, "GET", "/api/v1/mail")
        self.assertEqual(status, 403)

    def test_operativa_no_puede_aprobar(self):
        mail_id = mail_entrante.registrar_mail_entrante("consultorio", "paciente@ejemplo.com", "Turno", "Quiero un turno")["id"]
        status, data = self._as(self.operativa, "POST", f"/api/v1/mail/{mail_id}/aprobar", {"texto_final": "x"})
        self.assertEqual(status, 403)

    def test_operativa_no_puede_rechazar(self):
        mail_id = mail_entrante.registrar_mail_entrante("consultorio", "paciente@ejemplo.com", "Turno", "Quiero un turno")["id"]
        status, data = self._as(self.operativa, "POST", f"/api/v1/mail/{mail_id}/rechazar")
        self.assertEqual(status, 403)


class TestGenerarBorrador(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        _reset_db(self.db_file.name)

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("ai_router.clasificar_y_redactar_mail")
    def test_clasificacion_exitosa_deja_borrador_generado(self, mock_clasificar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {"categoria": "turno", "borrador_respuesta": "En breve te confirmamos un horario."},
        }
        mail_id = mail_entrante.registrar_mail_entrante("consultorio", "paciente@ejemplo.com", "Turno", "Quiero un turno")["id"]
        resultado = mail_entrante.generar_borrador(mail_id)
        self.assertTrue(resultado["ok"])

        mails = mail_entrante.list_mails(estado="borrador_generado")
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails[0]["categoria"], "turno")

    @patch("ai_router.clasificar_y_redactar_mail")
    def test_clasificacion_fallida_no_pierde_el_mail(self, mock_clasificar):
        mock_clasificar.return_value = {"outcome": "both_failed", "error": "claude: rate limit | openai: rate limit"}
        mail_id = mail_entrante.registrar_mail_entrante("abate", "alguien@ejemplo.com", "Consulta", "Necesito ayuda")["id"]
        resultado = mail_entrante.generar_borrador(mail_id)
        self.assertFalse(resultado["ok"])

        mails = mail_entrante.list_mails(estado="error_clasificacion")
        self.assertEqual(len(mails), 1)
        self.assertEqual(mails[0]["cuerpo_original"], "Necesito ayuda")

    @patch("ai_router.clasificar_y_redactar_mail")
    def test_spam_deja_borrador_vacio(self, mock_clasificar):
        mock_clasificar.return_value = {"outcome": "success", "data": {"categoria": "spam", "borrador_respuesta": ""}}
        mail_id = mail_entrante.registrar_mail_entrante("consultorio", "spam@ejemplo.com", "Oferta", "Comprá ahora")["id"]
        mail_entrante.generar_borrador(mail_id)
        mails = mail_entrante.list_mails(estado="borrador_generado")
        self.assertEqual(mails[0]["categoria"], "spam")
        self.assertEqual(mails[0]["borrador_respuesta"], "")


class TestAprobarYRechazar(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        _reset_db(self.db_file.name)

    def tearDown(self):
        os.unlink(self.db_file.name)

    def _mail_con_borrador(self, casilla="consultorio", remitente="paciente@ejemplo.com"):
        mail_id = mail_entrante.registrar_mail_entrante(casilla, remitente, "Turno", "Quiero un turno")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE mail_entrante SET categoria='turno', borrador_respuesta='Te confirmamos un horario en breve.', "
            "estado='borrador_generado' WHERE id=?",
            (mail_id,),
        )
        conn.commit()
        return mail_id

    @patch("gmail_client.enviar_respuesta")
    def test_aprobar_manda_por_gmail_y_marca_enviado(self, mock_enviar):
        mock_enviar.return_value = {"id": "xxxx"}
        mail_id = self._mail_con_borrador()
        resultado = mail_entrante.aprobar_y_enviar(mail_id, "nicolas", "director")
        self.assertTrue(resultado["ok"])
        mock_enviar.assert_called_once()

        mail = mail_entrante.list_mails(estado="aprobado_enviado")[0]
        self.assertEqual(mail["respuesta_final"], "Te confirmamos un horario en breve.")

    @patch("gmail_client.enviar_respuesta")
    def test_aprobar_con_texto_editado_manda_el_texto_editado(self, mock_enviar):
        mock_enviar.return_value = {"id": "xxxx"}
        mail_id = self._mail_con_borrador()
        mail_entrante.aprobar_y_enviar(mail_id, "nicolas", "director", texto_final="Texto editado a mano.")
        mock_enviar.assert_called_once_with("consultorio", "paciente@ejemplo.com", "Turno", "Texto editado a mano.", thread_id=None)

    @patch("gmail_client.enviar_respuesta")
    def test_operativa_no_puede_aprobar_nunca(self, mock_enviar):
        mail_id = self._mail_con_borrador()
        with self.assertRaises(mail_entrante.RequiereDirector):
            mail_entrante.aprobar_y_enviar(mail_id, "marianela", "operativa")
        mock_enviar.assert_not_called()

    @patch("gmail_client.enviar_respuesta")
    def test_rechazar_nunca_manda_nada(self, mock_enviar):
        mail_id = self._mail_con_borrador()
        resultado = mail_entrante.rechazar(mail_id, "nicolas", "director")
        self.assertTrue(resultado["ok"])
        mock_enviar.assert_not_called()
        mail = mail_entrante.list_mails(estado="rechazado")[0]
        self.assertEqual(mail["id"], mail_id)

    def test_operativa_no_puede_rechazar_nunca(self):
        mail_id = self._mail_con_borrador()
        with self.assertRaises(mail_entrante.RequiereDirector):
            mail_entrante.rechazar(mail_id, "marianela", "operativa")

    def test_no_se_puede_aprobar_dos_veces(self):
        mail_id = self._mail_con_borrador()
        with patch("gmail_client.enviar_respuesta", return_value={"id": "x"}):
            mail_entrante.aprobar_y_enviar(mail_id, "nicolas", "director")
        with self.assertRaises(mail_entrante.MailEntranteError):
            mail_entrante.aprobar_y_enviar(mail_id, "nicolas", "director")


class TestSincronizarCasilla(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        _reset_db(self.db_file.name)

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("gmail_client.list_mensajes_nuevos")
    def test_sincronizar_registra_mails_nuevos(self, mock_list):
        mock_list.return_value = [
            {"gmail_message_id": "abc123", "thread_id": "t1", "remitente": "juan@ejemplo.com", "asunto": "Turno", "cuerpo": "Hola"},
        ]
        resultado = mail_entrante.sincronizar_casilla("consultorio")
        self.assertEqual(resultado["nuevos"], 1)
        self.assertEqual(len(mail_entrante.list_mails(casilla="consultorio")), 1)

    @patch("gmail_client.list_mensajes_nuevos")
    def test_sincronizar_no_duplica_el_mismo_mail(self, mock_list):
        mock_list.return_value = [
            {"gmail_message_id": "abc123", "thread_id": "t1", "remitente": "juan@ejemplo.com", "asunto": "Turno", "cuerpo": "Hola"},
        ]
        mail_entrante.sincronizar_casilla("consultorio")
        resultado = mail_entrante.sincronizar_casilla("consultorio")
        self.assertEqual(resultado["nuevos"], 0)
        self.assertEqual(len(mail_entrante.list_mails(casilla="consultorio")), 1)

    @patch("gmail_client.list_mensajes_nuevos", side_effect=gmail_client.GmailConfigError("sin credenciales"))
    def test_worker_no_falla_si_una_casilla_no_esta_configurada(self, mock_list):
        # No debe levantar -- ver worker._sincronizar_mail_si_corresponde.
        worker._ultimo_sync_mail = None
        worker._sincronizar_mail_si_corresponde()


class TestWorkerGlueMail(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        _reset_db(self.db_file.name)
        worker._ultimo_chequeo_mail = None

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("ai_router.clasificar_y_redactar_mail")
    def test_worker_genera_borrador_para_mails_recibidos(self, mock_clasificar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {"categoria": "administrativo", "borrador_respuesta": "Atendemos de lunes a viernes de 8 a 13hs."},
        }
        mail_entrante.registrar_mail_entrante("consultorio", "alguien@ejemplo.com", "Horarios", "A qué hora atienden?")

        worker._procesar_mail_si_corresponde()

        mock_clasificar.assert_called_once()
        mails = mail_entrante.list_mails(estado="borrador_generado")
        self.assertEqual(len(mails), 1)


if __name__ == "__main__":
    unittest.main()
