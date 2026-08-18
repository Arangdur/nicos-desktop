"""
Bandeja de pedidos de factura por WhatsApp (ARCA) -- mismo patrón de prueba
que test_mensajes_whatsapp.py: base de datos temporal, IA mockeada, y acá
ADEMÁS los tres servicios externos reales (WSAA/WSFE de ARCA, Twilio) van
siempre mockeados -- ni un test de este archivo pega contra ARCA ni Twilio
de verdad. `pdf_generator` sí corre real (es puro local: fpdf2 + segno, sin
red, ver sidecar/facturacion/pdf_generator.py) pero escribiendo a un
directorio temporal, nunca a sidecar/facturas_pdf/ real.

Cubre:
1. generar_borrador: éxito dispara 'borrador_generado'; fallo de la IA nunca
   pierde el pedido, queda en 'error_extraccion' con el texto original.
2. aprobar_y_emitir: SIEMPRE Director-only, sin la excepción "no clínico"
   que tiene mensajes_whatsapp -- Operativa nunca puede, ni llamando el
   módulo directo (RequiereDirector) ni por HTTP (403).
3. ARCA rechaza (WsfeError) -> estado 'error_emision', nunca se manda nada
   por WhatsApp, la excepción se propaga como FacturaError.
4. Nota de crédito: exige factura_asociada_id, y esa factura tiene que
   estar 'emitida' -- si no, FacturaError antes de tocar ARCA.
5. rechazar: nunca emite nada, Director-only.
6. El glue del worker genera borradores para todo lo 'recibido'.

Uso: python3 sidecar/tests/test_facturas.py
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
os.environ["TWILIO_ACCOUNT_SID"] = "ACtest"
os.environ["TWILIO_WHATSAPP_FROM"] = "+14155238886"

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402
import facturas  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402
import worker  # noqa: E402
from facturacion import wsaa_client, wsfe_client  # noqa: E402

db.run_migrations()

CAE_OK = {"cae": "75123456789012", "vencimiento": "20260901", "resultado": "A"}


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


def _reset_a_la_db_compartida():
    os.environ["NICOS_DB_PATH"] = _TMP_DB.name
    import importlib
    importlib.reload(db)
    db.run_migrations()


class _BaseDbTemporal(unittest.TestCase):
    """Base para las clases que necesitan su PROPIA base (no la compartida
    de los tests HTTP) -- mismo patrón que TestGenerarBorrador en
    test_mensajes_whatsapp.py. También aisla PDF_DIR a un directorio
    temporal: sin esto, aprobar_y_emitir en un test escribiría de verdad
    en sidecar/facturas_pdf/, al lado del PDF real de una factura emitida
    de verdad."""

    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        import importlib
        importlib.reload(db)
        db.run_migrations()

        self.pdf_dir = tempfile.mkdtemp()
        self._pdf_dir_patch = patch.object(facturas, "PDF_DIR", self.pdf_dir)
        self._pdf_dir_patch.start()

    def tearDown(self):
        self._pdf_dir_patch.stop()
        os.unlink(self.db_file.name)
        import shutil
        shutil.rmtree(self.pdf_dir, ignore_errors=True)


class TestGenerarBorrador(_BaseDbTemporal):
    @patch("ai_router.extraer_pedido_factura")
    def test_extraccion_exitosa_deja_borrador_generado(self, mock_extraer):
        mock_extraer.return_value = {
            "outcome": "success",
            "data": {
                "monto": 45000.0, "concepto": "Consulta particular",
                "cliente_nombre": "Mariana Gauna", "cliente_doc_tipo": 96, "cliente_doc_nro": 30123456,
                "es_nota_credito": False,
            },
        }
        factura_id = facturas.registrar_pedido_factura("+5493513334455", "facturale 45000 a Mariana Gauna")["id"]
        resultado = facturas.generar_borrador(factura_id)
        self.assertTrue(resultado["ok"])

        pendientes = facturas.list_facturas("borrador_generado")
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["monto"], 45000.0)
        self.assertEqual(pendientes[0]["cliente_nombre"], "Mariana Gauna")
        self.assertFalse(pendientes[0]["es_nota_credito"])

    @patch("ai_router.extraer_pedido_factura")
    def test_extraccion_fallida_no_pierde_el_pedido(self, mock_extraer):
        mock_extraer.return_value = {"outcome": "both_failed", "error": "claude: rate limit | openai: rate limit"}
        factura_id = facturas.registrar_pedido_factura("+5493513334456", "facturale algo a alguien")["id"]
        resultado = facturas.generar_borrador(factura_id)
        self.assertFalse(resultado["ok"])

        pendientes = facturas.list_facturas("error_extraccion")
        self.assertEqual(len(pendientes), 1)
        self.assertEqual(pendientes[0]["texto_original"], "facturale algo a alguien")

    def test_registrar_sin_telefono_o_texto_falla(self):
        with self.assertRaises(facturas.FacturaError):
            facturas.registrar_pedido_factura("", "algo")
        with self.assertRaises(facturas.FacturaError):
            facturas.registrar_pedido_factura("+5493513334455", "")


class TestAprobarYEmitir(_BaseDbTemporal):
    def _pedido_con_borrador(self, monto=45000.0, es_nota_credito=False, telefono="+5493513330001"):
        factura_id = facturas.registrar_pedido_factura(telefono, "facturale algo")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE facturas_whatsapp SET monto=?, concepto='Consulta', cliente_nombre='Mariana Gauna', "
            "cliente_doc_tipo=96, cliente_doc_nro=30123456, es_nota_credito=?, estado='borrador_generado' "
            "WHERE id=?",
            (monto, int(es_nota_credito), factura_id),
        )
        conn.commit()
        return factura_id

    @patch("facturas.twilio_client.enviar_whatsapp_con_pdf")
    @patch.object(wsfe_client, "solicitar_cae")
    @patch.object(wsfe_client, "consultar_ultimo_autorizado")
    @patch.object(wsaa_client, "obtener_credenciales")
    def test_aprobar_emite_cae_real_y_genera_pdf(self, mock_creds, mock_ultimo, mock_cae, mock_twilio):
        mock_creds.return_value = {"token": "tok", "sign": "sig"}
        mock_ultimo.return_value = 7
        mock_cae.return_value = dict(CAE_OK)
        mock_twilio.return_value = {"sid": "SMxxxx"}

        factura_id = self._pedido_con_borrador()
        resultado = facturas.aprobar_y_emitir(factura_id, "nicolas", "director")

        self.assertTrue(resultado["ok"])
        self.assertEqual(resultado["cae"], CAE_OK["cae"])
        self.assertEqual(resultado["numero"], 8)  # último autorizado (7) + 1
        mock_twilio.assert_called_once()

        emitida = facturas.list_facturas("emitida")[0]
        self.assertEqual(emitida["cae"], CAE_OK["cae"])
        self.assertEqual(emitida["numero_cbte"], 8)
        self.assertTrue(os.path.exists(emitida["pdf_path"]))  # el PDF se escribió de verdad
        self.assertTrue(emitida["pdf_path"].startswith(self.pdf_dir))  # nunca al directorio real

    @patch.object(wsfe_client, "solicitar_cae")
    @patch.object(wsfe_client, "consultar_ultimo_autorizado")
    @patch.object(wsaa_client, "obtener_credenciales")
    def test_monto_editado_a_mano_se_usa_en_vez_del_borrador(self, mock_creds, mock_ultimo, mock_cae):
        mock_creds.return_value = {"token": "tok", "sign": "sig"}
        mock_ultimo.return_value = 0
        mock_cae.return_value = dict(CAE_OK)

        factura_id = self._pedido_con_borrador(monto=45000.0)
        with patch("facturas.twilio_client.enviar_whatsapp_con_pdf"):
            facturas.aprobar_y_emitir(factura_id, "nicolas", "director", monto_final=60000.0, concepto_final="Consulta editada")

        # El monto que se le pidió a ARCA es el editado, no el original del borrador.
        mock_cae.assert_called_once()
        self.assertEqual(mock_cae.call_args.args[6], 60000.0)  # importe posicional
        emitida = facturas.list_facturas("emitida")[0]
        self.assertEqual(emitida["monto"], 60000.0)
        self.assertEqual(emitida["concepto"], "Consulta editada")

    def test_operativa_no_puede_aprobar_ni_llamando_el_modulo_directo(self):
        factura_id = self._pedido_con_borrador()
        with self.assertRaises(facturas.RequiereDirector):
            facturas.aprobar_y_emitir(factura_id, "marianela", "operativa")
        # Nunca cambió de estado -- ni se acercó a llamar a ARCA.
        self.assertEqual(facturas.list_facturas("borrador_generado")[0]["id"], factura_id)

    @patch.object(wsfe_client, "solicitar_cae")
    @patch.object(wsfe_client, "consultar_ultimo_autorizado")
    @patch.object(wsaa_client, "obtener_credenciales")
    def test_arca_rechaza_deja_error_emision_y_nunca_manda_whatsapp(self, mock_creds, mock_ultimo, mock_cae):
        mock_creds.return_value = {"token": "tok", "sign": "sig"}
        mock_ultimo.return_value = 0
        mock_cae.side_effect = wsfe_client.WsfeError("CUIT no autorizado para este punto de venta")

        factura_id = self._pedido_con_borrador()
        with patch("facturas.twilio_client.enviar_whatsapp_con_pdf") as mock_twilio:
            with self.assertRaises(facturas.FacturaError):
                facturas.aprobar_y_emitir(factura_id, "nicolas", "director")
            mock_twilio.assert_not_called()

        fallida = facturas.list_facturas("error_emision")[0]
        self.assertEqual(fallida["id"], factura_id)
        self.assertIn("CUIT no autorizado", fallida["error_detalle"])

    def test_no_se_puede_aprobar_sin_monto_valido(self):
        factura_id = self._pedido_con_borrador(monto=None)
        with self.assertRaises(facturas.FacturaError):
            facturas.aprobar_y_emitir(factura_id, "nicolas", "director")

    def test_no_se_puede_aprobar_dos_veces(self):
        factura_id = self._pedido_con_borrador()
        with patch.object(wsaa_client, "obtener_credenciales", return_value={"token": "t", "sign": "s"}), \
             patch.object(wsfe_client, "consultar_ultimo_autorizado", return_value=0), \
             patch.object(wsfe_client, "solicitar_cae", return_value=dict(CAE_OK)), \
             patch("facturas.twilio_client.enviar_whatsapp_con_pdf"):
            facturas.aprobar_y_emitir(factura_id, "nicolas", "director")
        with self.assertRaises(facturas.FacturaError):
            facturas.aprobar_y_emitir(factura_id, "nicolas", "director")

    @patch("facturas.twilio_client.enviar_whatsapp_con_pdf")
    @patch.object(wsfe_client, "solicitar_cae")
    @patch.object(wsfe_client, "consultar_ultimo_autorizado")
    @patch.object(wsaa_client, "obtener_credenciales")
    def test_falla_de_twilio_no_revierte_la_emision_ya_hecha(self, mock_creds, mock_ultimo, mock_cae, mock_twilio):
        """Una vez que ARCA dio el CAE, el comprobante existe fiscalmente --
        que falle el envío por WhatsApp después NO puede hacer que la
        factura quede en un estado de error (no hay forma de deshacer una
        emisión real)."""
        mock_creds.return_value = {"token": "tok", "sign": "sig"}
        mock_ultimo.return_value = 0
        mock_cae.return_value = dict(CAE_OK)
        import twilio_client
        mock_twilio.side_effect = twilio_client.TwilioSendError("Twilio no pudo descargar el PDF")

        factura_id = self._pedido_con_borrador()
        resultado = facturas.aprobar_y_emitir(factura_id, "nicolas", "director")

        self.assertTrue(resultado["ok"])
        self.assertFalse(resultado["envio_whatsapp_ok"])
        self.assertIn("Twilio", resultado["envio_whatsapp_error"])
        # El estado fiscal sigue siendo 'emitida', no 'error_emision'.
        self.assertEqual(facturas.list_facturas("emitida")[0]["id"], factura_id)


class TestNotaDeCredito(_BaseDbTemporal):
    def _factura_emitida(self, numero=1):
        factura_id = facturas.registrar_pedido_factura("+5493513330002", "facturale 30000 a Juan")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE facturas_whatsapp SET monto=30000, concepto='Consulta', cliente_nombre='Juan Perez', "
            "estado='emitida', pto_venta=4, tipo_cbte=11, numero_cbte=?, cae='CAEPREVIO', "
            "cae_vencimiento='20260901' WHERE id=?",
            (numero, factura_id),
        )
        conn.commit()
        return factura_id

    def _nota_credito_con_borrador(self, factura_asociada_default=None):
        factura_id = facturas.registrar_pedido_factura("+5493513330003", "nota de credito de la factura de Juan")["id"]
        conn = db.get_connection()
        conn.execute(
            "UPDATE facturas_whatsapp SET monto=30000, concepto='Anulación', cliente_nombre='Juan Perez', "
            "es_nota_credito=1, estado='borrador_generado' WHERE id=?",
            (factura_id,),
        )
        conn.commit()
        return factura_id

    def test_nota_de_credito_sin_factura_asociada_falla(self):
        nc_id = self._nota_credito_con_borrador()
        with self.assertRaises(facturas.FacturaError):
            facturas.aprobar_y_emitir(nc_id, "nicolas", "director")

    def test_nota_de_credito_referenciando_factura_no_emitida_falla(self):
        # Una factura que todavía es solo un borrador, nunca se emitió.
        factura_id = facturas.registrar_pedido_factura("+5493513330004", "facturale algo")["id"]
        nc_id = self._nota_credito_con_borrador()
        with self.assertRaises(facturas.FacturaError):
            facturas.aprobar_y_emitir(nc_id, "nicolas", "director", factura_asociada_id=factura_id)

    @patch("facturas.twilio_client.enviar_whatsapp_con_pdf")
    @patch.object(wsfe_client, "solicitar_cae")
    @patch.object(wsfe_client, "consultar_ultimo_autorizado")
    @patch.object(wsaa_client, "obtener_credenciales")
    def test_nota_de_credito_valida_referencia_el_comprobante_asociado(self, mock_creds, mock_ultimo, mock_cae, mock_twilio):
        mock_creds.return_value = {"token": "tok", "sign": "sig"}
        mock_ultimo.return_value = 0
        mock_cae.return_value = {"cae": "75999999999999", "vencimiento": "20260901", "resultado": "A"}
        mock_twilio.return_value = {"sid": "SMxxxx"}

        factura_id = self._factura_emitida(numero=5)
        nc_id = self._nota_credito_con_borrador()
        resultado = facturas.aprobar_y_emitir(nc_id, "nicolas", "director", factura_asociada_id=factura_id)

        self.assertTrue(resultado["ok"])
        # Se le pasó a ARCA el comprobante asociado correcto (tipo 11, pto_venta 4, número 5).
        kwargs = mock_cae.call_args.kwargs
        self.assertEqual(kwargs["comprobantes_asociados"], [{"tipo": 11, "pto_venta": 4, "numero": 5}])
        nc_emitida = facturas.list_facturas("emitida")
        self.assertEqual(len([f for f in nc_emitida if f["id"] == nc_id]), 1)


class TestRechazar(_BaseDbTemporal):
    def _pedido_con_borrador(self):
        factura_id = facturas.registrar_pedido_factura("+5493513330005", "facturale algo")["id"]
        conn = db.get_connection()
        conn.execute("UPDATE facturas_whatsapp SET monto=1000, estado='borrador_generado' WHERE id=?", (factura_id,))
        conn.commit()
        return factura_id

    def test_rechazar_nunca_emite_nada(self):
        factura_id = self._pedido_con_borrador()
        with patch.object(wsaa_client, "obtener_credenciales") as mock_creds:
            resultado = facturas.rechazar(factura_id, "nicolas", "director")
            mock_creds.assert_not_called()
        self.assertTrue(resultado["ok"])
        self.assertEqual(facturas.list_facturas("rechazada")[0]["id"], factura_id)

    def test_operativa_no_puede_rechazar(self):
        factura_id = self._pedido_con_borrador()
        with self.assertRaises(facturas.RequiereDirector):
            facturas.rechazar(factura_id, "marianela", "operativa")


class TestWorkerGlueFacturas(_BaseDbTemporal):
    def setUp(self):
        super().setUp()
        worker._ultimo_chequeo_facturas = None

    @patch("ai_router.extraer_pedido_factura")
    def test_worker_genera_borrador_para_pedidos_recibidos(self, mock_extraer):
        mock_extraer.return_value = {
            "outcome": "success",
            "data": {
                "monto": 20000.0, "concepto": "Consulta", "cliente_nombre": None,
                "cliente_doc_tipo": 99, "cliente_doc_nro": 0, "es_nota_credito": False,
            },
        }
        facturas.registrar_pedido_factura("+5493513330006", "facturale 20000 a consumidor final")

        worker._procesar_facturas_si_corresponde()

        mock_extraer.assert_called_once()
        self.assertEqual(len(facturas.list_facturas("borrador_generado")), 1)


class TestFacturasHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        _reset_a_la_db_compartida()
        cls.httpd = _make_server(is_lan=True)
        cls.operativa = _pair("operativa", "Marianela Prueba Facturas")

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
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
        return self._request(self.httpd, method, path, body, token=identity["token"])

    def test_operativa_puede_ver_la_bandeja(self):
        status, data = self._as(self.operativa, "GET", "/api/v1/facturas")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_operativa_no_puede_aprobar_por_http(self):
        factura_id = facturas.registrar_pedido_factura("+5493513330007", "facturale algo")["id"]
        conn = db.get_connection()
        conn.execute("UPDATE facturas_whatsapp SET monto=1000, estado='borrador_generado' WHERE id=?", (factura_id,))
        conn.commit()
        status, data = self._as(self.operativa, "POST", f"/api/v1/facturas/{factura_id}/aprobar", {"monto_final": 1000})
        self.assertEqual(status, 403)

    def test_operativa_no_puede_rechazar_por_http(self):
        factura_id = facturas.registrar_pedido_factura("+5493513330008", "facturale algo")["id"]
        status, data = self._as(self.operativa, "POST", f"/api/v1/facturas/{factura_id}/rechazar")
        self.assertEqual(status, 403)

    def test_pdf_de_factura_no_emitida_da_404(self):
        factura_id = facturas.registrar_pedido_factura("+5493513330009", "facturale algo")["id"]
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("GET", f"/facturas/{factura_id}/pdf")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        conn.close()

    def test_pdf_de_id_inexistente_da_404(self):
        conn = http.client.HTTPConnection("127.0.0.1", self.httpd.server_address[1], timeout=5)
        conn.request("GET", "/facturas/idquenoexiste/pdf")
        resp = conn.getresponse()
        self.assertEqual(resp.status, 404)
        conn.close()


if __name__ == "__main__":
    unittest.main()
