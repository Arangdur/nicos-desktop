"""
Bandeja de mensajes de WhatsApp ENTRANTES -- prueba con evidencia real (HTTP
contra servidores is_lan=True/False con tokens de verdad, igual que
test_recordatorios.py) que:

1. La firma de Twilio se verifica correctamente (rechaza inválida, acepta
   válida) -- ver whatsapp_inbound.py.
2. /whatsapp/inbound registra el mensaje SIN autenticación de NicOS (no usa
   is_lan()) -- el único límite es la firma.
3. La IA (mockeada, nunca red real) clasifica y arma un borrador -- si falla,
   el mensaje NUNCA se pierde, queda en 'error_clasificacion' con el texto
   original intacto.
4. Aprobar y enviar manda por Twilio (mockeado) SOLO si hay un borrador
   pendiente, y solo el Director puede aprobar algo `requiere_profesional`
   (Operativa recibe 403, tanto llamando el módulo directo como por HTTP).
5. Rechazar nunca manda nada por Twilio.
6. El glue del worker genera borradores para todo lo que esté 'recibido'.

Usa base de datos temporal -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_mensajes_whatsapp.py
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
os.environ["NICOS_TWILIO_WEBHOOK_BASE_URL"] = "https://test.tailnet.ts.net"
os.environ["TWILIO_AUTH_TOKEN"] = "token_de_prueba_12345"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402
import mensajes_whatsapp  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402
import twilio_client  # noqa: E402
import whatsapp_inbound  # noqa: E402
import worker  # noqa: E402

db.run_migrations()

WEBHOOK_URL = "https://test.tailnet.ts.net/whatsapp/inbound"
AUTH_TOKEN = "token_de_prueba_12345"


def _firma_valida(params):
    import base64, hashlib, hmac
    data = WEBHOOK_URL
    for k in sorted(params.keys()):
        data += k + params[k]
    return base64.b64encode(hmac.new(AUTH_TOKEN.encode(), data.encode(), hashlib.sha1).digest()).decode()


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


class TestFirmaTwilio(unittest.TestCase):
    def test_firma_valida_pasa(self):
        params = {"From": "whatsapp:+5493584390917", "Body": "Hola quiero un turno"}
        firma = _firma_valida(params)
        self.assertTrue(whatsapp_inbound.verificar_firma(WEBHOOK_URL, params, firma, AUTH_TOKEN))

    def test_firma_invalida_rechazada(self):
        params = {"From": "whatsapp:+5493584390917", "Body": "Hola"}
        self.assertFalse(whatsapp_inbound.verificar_firma(WEBHOOK_URL, params, "firma_inventada", AUTH_TOKEN))

    def test_url_distinta_invalida_la_firma(self):
        params = {"From": "whatsapp:+5493584390917", "Body": "Hola"}
        firma = _firma_valida(params)
        otra_url = "https://otro-dominio.ts.net/whatsapp/inbound"
        self.assertFalse(whatsapp_inbound.verificar_firma(otra_url, params, firma, AUTH_TOKEN))

    def test_parsear_form_body_extrae_telefono_y_texto(self):
        raw = b"From=whatsapp%3A%2B5493584390917&Body=Hola%2C+quiero+un+turno&MessageSid=SMxxxx"
        params = whatsapp_inbound.parsear_form_body(raw)
        telefono, texto = whatsapp_inbound.extraer_telefono_y_texto(params)
        self.assertEqual(telefono, "+5493584390917")
        self.assertEqual(texto, "Hola, quiero un turno")


def _reset_a_la_db_compartida():
    """Las clases con base temporal propia (TestGenerarBorrador,
    TestAprobarYRechazar, TestWorkerGlueMensajes) hacen `importlib.reload(db)`
    apuntando a SU propio archivo y lo borran en tearDown -- unittest no
    garantiza que esas clases corran después de las que usan `_TMP_DB`
    (el orden es alfabético por nombre de clase, no de definición), así que
    estas clases HTTP-con-servidor-compartido se paran de nuevo sobre
    `_TMP_DB` explícitamente en su propio setUpClass, en vez de asumir que
    nadie tocó el estado global antes -- más robusto que pelearse con el
    orden alfabético."""
    os.environ["NICOS_DB_PATH"] = _TMP_DB.name
    import importlib
    importlib.reload(db)
    db.run_migrations()


class TestWebhookInboundHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _reset_a_la_db_compartida()
        cls.httpd = _make_server(is_lan=True)  # /whatsapp/inbound no usa is_lan de todos modos

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def _post_form(self, form_body: bytes, signature: str):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        headers = {"Content-Type": "application/x-www-form-urlencoded"}
        if signature is not None:
            headers["X-Twilio-Signature"] = signature
        conn.request("POST", "/whatsapp/inbound", body=form_body, headers=headers)
        resp = conn.getresponse()
        body = resp.read()
        conn.close()
        return resp.status, body

    def test_firma_invalida_rechazada_con_403(self):
        status, body = self._post_form(b"From=whatsapp%3A%2B5493584390000&Body=test", "firma_invalida")
        self.assertEqual(status, 403)

    def test_firma_valida_registra_el_mensaje(self):
        params = {"From": "whatsapp:+5493584390001", "Body": "Quiero sacar un turno para la semana que viene"}
        firma = _firma_valida(params)
        import urllib.parse
        form_body = urllib.parse.urlencode(params).encode()
        status, body = self._post_form(form_body, firma)
        self.assertEqual(status, 200)
        # No es JSON -- Twilio espera TwiML.
        self.assertIn(b"<Response", body)

        mensajes = mensajes_whatsapp.list_mensajes("recibido")
        coincidencias = [m for m in mensajes if m["telefono"] == "+5493584390001"]
        self.assertEqual(len(coincidencias), 1)
        self.assertEqual(coincidencias[0]["texto_original"], "Quiero sacar un turno para la semana que viene")


class TestMensajesWhatsappHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _reset_a_la_db_compartida()
        cls.lan_httpd = _make_server(is_lan=True)
        cls.operativa = _pair("operativa", "Marianela Prueba Mensajes")
        cls.enfermero = _pair("enfermero", "Enfermero Prueba Mensajes")

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

    def test_operativa_puede_ver_la_bandeja(self):
        status, data = self._as(self.operativa, "GET", "/api/v1/whatsapp/mensajes")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_enfermero_no_puede_ver_la_bandeja(self):
        status, data = self._as(self.enfermero, "GET", "/api/v1/whatsapp/mensajes")
        self.assertEqual(status, 403)

    def test_operativa_no_puede_aprobar_mensaje_que_requiere_profesional(self):
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390099", "Necesito una receta de mi medicación")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET clasificacion='receta', requiere_profesional=1, "
            "borrador_respuesta='El Dr. Buso va a revisar tu pedido.', estado='borrador_generado' WHERE id=?",
            (mensaje_id,),
        )
        conn.commit()
        status, data = self._as(self.operativa, "POST", f"/api/v1/whatsapp/mensajes/{mensaje_id}/aprobar", {"texto_final": None})
        self.assertEqual(status, 403)


class TestGenerarBorrador(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        import importlib
        importlib.reload(db)
        db.run_migrations()

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_clasificacion_exitosa_deja_borrador_generado(self, mock_clasificar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "turno_nuevo", "requiere_profesional": False, "urgente": False,
                "borrador_respuesta": "Hola! En breve te confirmamos un horario.",
            },
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390002", "Quiero un turno")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertTrue(resultado["ok"])

        mensajes = mensajes_whatsapp.list_mensajes("borrador_generado")
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes[0]["clasificacion"], "turno_nuevo")
        self.assertFalse(mensajes[0]["requiere_profesional"])

    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_clasificacion_fallida_no_pierde_el_mensaje(self, mock_clasificar):
        mock_clasificar.return_value = {"outcome": "both_failed", "error": "claude: rate limit | openai: rate limit"}
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390003", "Hola necesito ayuda")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertFalse(resultado["ok"])

        mensajes = mensajes_whatsapp.list_mensajes("error_clasificacion")
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes[0]["texto_original"], "Hola necesito ayuda")
        # Estado sin clasificar se trata como requiere_profesional por las dudas.
        self.assertTrue(mensajes[0]["requiere_profesional"])

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_saludo_puro_se_manda_solo(self, mock_clasificar, mock_enviar):
        # v0.2.6 (20/08) -- pedido real de Nicolás: única excepción a "nada
        # sale sin aprobación" -- un saludo puro (ambiguo, sin
        # requiere_profesional, sin urgencia, sin acción de DrApp) se manda
        # directo, para que la respuesta sea rápida.
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "ambiguo", "requiere_profesional": False, "urgente": False,
                "borrador_respuesta": "¡Hola! ¿en qué te puedo ayudar?",
            },
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390005", "hola buen dia")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertTrue(resultado["ok"])
        self.assertTrue(resultado.get("auto_enviado"))
        mock_enviar.assert_called_once_with("+5493584390005", "¡Hola! ¿en qué te puedo ayudar?")

        mensajes = mensajes_whatsapp.list_mensajes("aprobado_enviado")
        self.assertEqual(len(mensajes), 1)
        self.assertEqual(mensajes[0]["resuelto_by"], "sistema")
        self.assertEqual(mensajes[0]["respuesta_final"], "¡Hola! ¿en qué te puedo ayudar?")

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_ambiguo_urgente_no_se_manda_solo(self, mock_clasificar, mock_enviar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "ambiguo", "requiere_profesional": False, "urgente": True,
                "borrador_respuesta": "Ya avisamos al consultorio, te contactan ya mismo.",
            },
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390006", "urgente necesito ayuda")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertNotIn("auto_enviado", resultado)
        mock_enviar.assert_not_called()
        self.assertEqual(len(mensajes_whatsapp.list_mensajes("borrador_generado")), 1)

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_receta_manda_el_acuse_fijo_solo(self, mock_clasificar, mock_enviar):
        # v0.2.7 (20/08) -- pedido real de Nicolás: el acuse de un pedido de
        # receta se manda solo, con texto FIJO (no el que arma la IA) --
        # sigue marcado requiere_profesional=true (fue clínico), pero eso ya
        # no bloquea el auto-envío del acuse -- la receta en sí sigue
        # gestionándose fuera de este sistema, como siempre.
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": False,
                "borrador_respuesta": "El Dr. Buso va a revisar tu pedido personalmente.",
            },
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390004", "Necesito que me renueves la receta")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertTrue(resultado.get("auto_enviado"))
        mock_enviar.assert_called_once_with("+5493584390004", mensajes_whatsapp.ACUSE_RECETA)

        mensajes = mensajes_whatsapp.list_mensajes("aprobado_enviado")
        self.assertEqual(len(mensajes), 1)
        self.assertTrue(mensajes[0]["requiere_profesional"])
        self.assertEqual(mensajes[0]["resuelto_by"], "sistema")
        self.assertEqual(mensajes[0]["respuesta_final"], mensajes_whatsapp.ACUSE_RECETA)

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_receta_reenvia_el_pedido_al_consultorio(self, mock_clasificar, mock_enviar):
        # v0.2.7 (20/08) -- pedido real de Nicolás: además de contestarle
        # al paciente, reenviar el pedido al WhatsApp que Marianela atiende
        # para que se contacte y siga el trámite -- la receta en sí sigue
        # gestionándose exactamente igual que hasta ahora, esto solo la avisa.
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": False,
                "borrador_respuesta": "irrelevante -- se pisa con el acuse fijo",
            },
        }
        with patch.dict(os.environ, {"CONSULTORIO_WHATSAPP_NUMERO": "+5493537599192"}):
            mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390004", "Necesito la receta de sertralina")["id"]
            mensajes_whatsapp.generar_borrador(mensaje_id)

        self.assertEqual(mock_enviar.call_count, 2)
        mock_enviar.assert_any_call("+5493584390004", mensajes_whatsapp.ACUSE_RECETA)
        segunda_llamada = mock_enviar.call_args_list[1]
        self.assertEqual(segunda_llamada[0][0], "+5493537599192")
        self.assertIn("+5493584390004", segunda_llamada[0][1])
        self.assertIn("Necesito la receta de sertralina", segunda_llamada[0][1])

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_receta_sin_numero_de_consultorio_configurado_no_reenvia(self, mock_clasificar, mock_enviar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": False,
                "borrador_respuesta": "irrelevante -- se pisa con el acuse fijo",
            },
        }
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("CONSULTORIO_WHATSAPP_NUMERO", None)
            mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390004", "Necesito una receta")["id"]
            mensajes_whatsapp.generar_borrador(mensaje_id)
        mock_enviar.assert_called_once_with("+5493584390004", mensajes_whatsapp.ACUSE_RECETA)

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_receta_urgente_tambien_manda_el_acuse_solo(self, mock_clasificar, mock_enviar):
        # A diferencia del saludo puro, el acuse de receta se manda igual
        # aunque venga marcado urgente -- no compromete nada clínico, y un
        # pedido urgente se beneficia más todavía de una respuesta rápida.
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": True,
                "borrador_respuesta": "irrelevante -- se pisa con el acuse fijo",
            },
        }
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante("+5493584390009", "urgente necesito receta")["id"]
        resultado = mensajes_whatsapp.generar_borrador(mensaje_id)
        self.assertTrue(resultado.get("auto_enviado"))
        mock_enviar.assert_called_once_with("+5493584390009", mensajes_whatsapp.ACUSE_RECETA)

    @patch("twilio_client.enviar_whatsapp")
    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_segundo_mensaje_de_receta_lo_contesta_la_ia_libre_y_lo_manda(self, mock_clasificar, mock_enviar):
        # v0.2.7 (20/08) -- hallazgo real: un paciente preguntó "¿cómo
        # sabés qué receta necesito?" -- la IA lo volvió a clasificar como
        # 'receta' (menciona la palabra) y el acuse fijo se mandó de nuevo,
        # textual, sin contestar nada. Decisión explícita de Nicolás: el
        # PRIMER contacto manda el acuse fijo; un mensaje de seguimiento
        # dentro de la ventana también se manda solo, pero con lo que la
        # IA redactó para ESE mensaje puntual (contesta de verdad) -- única
        # excepción real a "la IA solo redacta, nunca manda sola".
        telefono = "+5493584390010"
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": False,
                "borrador_respuesta": "El Dr. Buso va a revisar tu pedido personalmente.",
            },
        }
        primero_id = mensajes_whatsapp.registrar_mensaje_entrante(telefono, "Hola quiero una receta")["id"]
        mensajes_whatsapp.generar_borrador(primero_id)
        mock_enviar.assert_called_once_with(telefono, mensajes_whatsapp.ACUSE_RECETA)

        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "receta", "requiere_profesional": True, "urgente": False,
                "borrador_respuesta": "¿Podrías decirme el nombre del medicamento?",
            },
        }
        segundo_id = mensajes_whatsapp.registrar_mensaje_entrante(telefono, "bueno gracias, pero como sabes que receta necesito?")["id"]
        resultado = mensajes_whatsapp.generar_borrador(segundo_id)
        self.assertTrue(resultado.get("auto_enviado"))
        mock_enviar.assert_called_with(telefono, "¿Podrías decirme el nombre del medicamento?")
        self.assertEqual(mock_enviar.call_count, 2)  # se mandaron los dos, distintos
        enviados = mensajes_whatsapp.list_mensajes("aprobado_enviado")
        self.assertEqual(len(enviados), 2)


class TestAprobarYRechazar(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        import importlib
        importlib.reload(db)
        db.run_migrations()

    def tearDown(self):
        os.unlink(self.db_file.name)

    def _mensaje_con_borrador(self, requiere_profesional=False, telefono="+5493584390005"):
        mensaje_id = mensajes_whatsapp.registrar_mensaje_entrante(telefono, "Quiero un turno")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE mensajes_whatsapp_entrantes SET clasificacion='turno_nuevo', requiere_profesional=?, "
            "borrador_respuesta='Te confirmamos un horario en breve.', estado='borrador_generado' WHERE id=?",
            (int(requiere_profesional), mensaje_id),
        )
        conn.commit()
        return mensaje_id

    @patch("twilio_client.enviar_whatsapp")
    def test_aprobar_manda_por_twilio_y_marca_enviado(self, mock_enviar):
        mock_enviar.return_value = {"sid": "SMxxxx", "status": "queued"}
        mensaje_id = self._mensaje_con_borrador()
        resultado = mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "nicolas", "director")
        self.assertTrue(resultado["ok"])
        mock_enviar.assert_called_once()

        mensaje = mensajes_whatsapp.list_mensajes("aprobado_enviado")[0]
        self.assertEqual(mensaje["respuesta_final"], "Te confirmamos un horario en breve.")

    @patch("twilio_client.enviar_whatsapp")
    def test_aprobar_con_texto_editado_manda_el_texto_editado_no_el_borrador(self, mock_enviar):
        mock_enviar.return_value = {"sid": "SMxxxx", "status": "queued"}
        mensaje_id = self._mensaje_con_borrador()
        mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "nicolas", "director", texto_final="Texto editado a mano.")
        mock_enviar.assert_called_once_with("+5493584390005", "Texto editado a mano.")

    @patch("twilio_client.enviar_whatsapp")
    def test_operativa_no_puede_aprobar_requiere_profesional(self, mock_enviar):
        mensaje_id = self._mensaje_con_borrador(requiere_profesional=True)
        with self.assertRaises(mensajes_whatsapp.RequiereProfesional):
            mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "marianela", "operativa")
        mock_enviar.assert_not_called()

    @patch("twilio_client.enviar_whatsapp")
    def test_director_si_puede_aprobar_requiere_profesional(self, mock_enviar):
        mock_enviar.return_value = {"sid": "SMxxxx", "status": "queued"}
        mensaje_id = self._mensaje_con_borrador(requiere_profesional=True)
        resultado = mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "nicolas", "director")
        self.assertTrue(resultado["ok"])

    @patch("twilio_client.enviar_whatsapp")
    def test_rechazar_nunca_manda_nada(self, mock_enviar):
        mensaje_id = self._mensaje_con_borrador()
        resultado = mensajes_whatsapp.rechazar(mensaje_id, "marianela")
        self.assertTrue(resultado["ok"])
        mock_enviar.assert_not_called()
        mensaje = mensajes_whatsapp.list_mensajes("rechazado")[0]
        self.assertEqual(mensaje["id"], mensaje_id)

    def test_no_se_puede_aprobar_dos_veces(self):
        mensaje_id = self._mensaje_con_borrador()
        with patch("twilio_client.enviar_whatsapp", return_value={"sid": "x", "status": "queued"}):
            mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "nicolas", "director")
        with self.assertRaises(mensajes_whatsapp.MensajeWhatsappError):
            mensajes_whatsapp.aprobar_y_enviar(mensaje_id, "nicolas", "director")


class TestWorkerGlueMensajes(unittest.TestCase):
    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        import importlib
        importlib.reload(db)
        db.run_migrations()
        worker._ultimo_chequeo_mensajes_whatsapp = None

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("ai_router.clasificar_y_redactar_mensaje")
    def test_worker_genera_borrador_para_mensajes_recibidos(self, mock_clasificar):
        mock_clasificar.return_value = {
            "outcome": "success",
            "data": {
                "clasificacion": "consulta_general", "requiere_profesional": False, "urgente": False,
                "borrador_respuesta": "Atendemos de lunes a viernes de 8 a 13hs.",
            },
        }
        mensajes_whatsapp.registrar_mensaje_entrante("+5493584390006", "A qué hora atienden?")

        worker._procesar_mensajes_whatsapp_si_corresponde()

        mock_clasificar.assert_called_once()
        mensajes = mensajes_whatsapp.list_mensajes("borrador_generado")
        self.assertEqual(len(mensajes), 1)


if __name__ == "__main__":
    unittest.main()
