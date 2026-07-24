"""
Recordatorio de turnos de Medicina General por WhatsApp -- prueba con
evidencia real (requests HTTP contra servidores con is_lan=True/False y
tokens de verdad) que:

1. Importar turnos es Director-only (bloqueado a nivel de servidor).
2. Ver la lista es Director + Operativa -- Enfermero recibe 403.
3. Un turno sin teléfono entra directo en estado 'sin_telefono', nunca se
   asume ni se inventa un número.
4. La ventana de envío (`turnos_para_enviar_ahora`) agarra cualquier turno
   pendiente a menos de ~25hs por delante (incluye los cargados con poca
   anticipación, que se mandan enseguida) y nunca uno que ya pasó. Todo en
   hora LOCAL -- compararlo contra UTC corría el envío ~3hs (bug corregido
   en la auditoría del 23/07).
5. Idempotencia: una vez marcado 'enviado', un turno no vuelve a aparecer
   para reenvío aunque siga dentro de la ventana.
6. El glue del worker (`_procesar_recordatorios_si_corresponde`) manda por
   Twilio (mockeado, nunca pega a la red real) y marca el resultado real.

Usa base de datos temporal -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_recordatorios.py
"""
import datetime
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
import pairing  # noqa: E402
import recordatorios  # noqa: E402
import server  # noqa: E402
import twilio_client  # noqa: E402
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


def _turno(**overrides):
    base = {
        "paciente_nombre": "Paciente, De Prueba",
        "fecha_turno": "2026-08-01",
        "hora_turno": "10:00",
        "cobertura": "PAMI",
        "practica": "Medicina General",
        "telefono": "5493537000000",
    }
    base.update(overrides)
    return base


class TestRecordatoriosHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.local_httpd = _make_server(is_lan=False)   # simula al Director en su propia Mac
        cls.lan_httpd = _make_server(is_lan=True)       # simula la red de Tailscale

        cls.operativa = _pair("operativa", "Marianela Prueba Recordatorios")
        cls.enfermero = _pair("enfermero", "Enfermero Prueba Recordatorios")

    @classmethod
    def tearDownClass(cls):
        cls.local_httpd.shutdown()
        cls.lan_httpd.shutdown()
        os.unlink(_TMP_DB.name)

    @staticmethod
    def _request_static(httpd, method, path, body=None, token=None):
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

    def _local(self, method, path, body=None):
        return self._request_static(self.local_httpd, method, path, body)

    def _as(self, identity, method, path, body=None):
        return self._request_static(self.lan_httpd, method, path, body, token=identity["token"])

    # 1. Director-only para importar --------------------------------------

    def test_director_importa_turno_ok(self):
        status, data = self._local("POST", "/api/v1/recordatorios/importar", {"turnos": [_turno()]})
        self.assertEqual(status, 200, data)
        self.assertEqual(data["importados"], 1)

    def test_operativa_no_puede_importar_por_red(self):
        status, data = self._as(self.operativa, "POST", "/api/v1/recordatorios/importar", {"turnos": [_turno()]})
        self.assertEqual(status, 403)

    def test_enfermero_no_puede_importar_por_red(self):
        status, data = self._as(self.enfermero, "POST", "/api/v1/recordatorios/importar", {"turnos": [_turno()]})
        self.assertEqual(status, 403)

    # 2. Director + Operativa pueden leer, Enfermero no ---------------------

    def test_operativa_puede_ver_lista(self):
        status, data = self._as(self.operativa, "GET", "/api/v1/recordatorios")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_enfermero_no_puede_ver_lista(self):
        status, data = self._as(self.enfermero, "GET", "/api/v1/recordatorios")
        self.assertEqual(status, 403)

    # 3. Sin teléfono => 'sin_telefono', nunca se inventa ---------------------

    def test_turno_sin_telefono_queda_sin_telefono(self):
        status, data = self._local(
            "POST", "/api/v1/recordatorios/importar",
            {"turnos": [_turno(paciente_nombre="Sin Telefono, Caso", telefono=None)]},
        )
        self.assertEqual(status, 200, data)
        recordatorio_id = data["ids"][0]
        fila = [r for r in recordatorios.list_recordatorios() if r["id"] == recordatorio_id][0]
        self.assertEqual(fila["estado"], "sin_telefono")

    def test_importar_sin_fecha_es_rechazado(self):
        status, data = self._local(
            "POST", "/api/v1/recordatorios/importar",
            {"turnos": [{"paciente_nombre": "Falta Fecha", "hora_turno": "10:00"}]},
        )
        self.assertEqual(status, 400)


class TestVentanaDeEnvio(unittest.TestCase):
    """No necesita servidor HTTP -- prueba directo la lógica de la ventana
    de envío (menos de ~25hs por delante, sin piso), con base de datos
    temporal propia."""

    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        db.get_connection.cache_clear() if hasattr(db.get_connection, "cache_clear") else None
        import importlib
        importlib.reload(db)
        db.run_migrations()
        pairing.start_pairing("operativa", created_by="nicolas", display_name="Setup")

    def tearDown(self):
        os.unlink(self.db_file.name)

    def _importar(self, **overrides):
        result = recordatorios.importar_turnos([_turno(**overrides)], created_by="nicolas")
        return result["ids"][0]

    def test_turno_a_24hs_exactas_entra_en_la_ventana(self):
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora + datetime.timedelta(hours=24)
        self._importar(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))
        candidatos = recordatorios.turnos_para_enviar_ahora(ahora)
        self.assertEqual(len(candidatos), 1)

    def test_turno_a_10hs_se_manda_enseguida(self):
        # Cargado con poca anticipación (ej. agendado por teléfono hoy para
        # mañana temprano) -- se manda en el próximo tick, no queda
        # 'pendiente' para siempre en silencio (bug de la primera versión,
        # corregido en la auditoría del 23/07).
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora + datetime.timedelta(hours=10)
        self._importar(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))
        candidatos = recordatorios.turnos_para_enviar_ahora(ahora)
        self.assertEqual(len(candidatos), 1)

    def test_turno_ya_pasado_no_se_manda(self):
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora - datetime.timedelta(hours=2)
        self._importar(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))
        candidatos = recordatorios.turnos_para_enviar_ahora(ahora)
        self.assertEqual(len(candidatos), 0)

    def test_turno_a_48hs_no_entra_todavia(self):
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora + datetime.timedelta(hours=48)
        self._importar(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))
        candidatos = recordatorios.turnos_para_enviar_ahora(ahora)
        self.assertEqual(len(candidatos), 0)

    def test_turno_sin_telefono_nunca_entra_en_la_ventana(self):
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora + datetime.timedelta(hours=24)
        self._importar(
            fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"), telefono=None,
        )
        candidatos = recordatorios.turnos_para_enviar_ahora(ahora)
        self.assertEqual(len(candidatos), 0)

    def test_idempotencia_turno_ya_enviado_no_se_reconsidera(self):
        ahora = datetime.datetime(2026, 8, 1, 10, 0)
        turno_dt = ahora + datetime.timedelta(hours=24)
        recordatorio_id = self._importar(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))
        self.assertEqual(len(recordatorios.turnos_para_enviar_ahora(ahora)), 1)

        recordatorios.marcar_resultado(recordatorio_id, "enviado")
        self.assertEqual(len(recordatorios.turnos_para_enviar_ahora(ahora)), 0)

    def test_marcar_resultado_estado_invalido_rechazado(self):
        recordatorio_id = self._importar()
        with self.assertRaises(recordatorios.RecordatorioError):
            recordatorios.marcar_resultado(recordatorio_id, "estado_que_no_existe")


class TestTwilioClient(unittest.TestCase):
    def test_sin_credenciales_configuradas_falla_claro(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(twilio_client.TwilioConfigError):
                twilio_client.enviar_whatsapp("5493537000000", "Hola")


class TestWorkerGlue(unittest.TestCase):
    """El pegamento del worker manda por Twilio (mockeado acá, nunca la red
    real) y marca el resultado -- confirma que la pieza que realmente se
    ejecuta en producción queda bien conectada, no solo sus partes sueltas."""

    def setUp(self):
        self.db_file = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.db_file.close()
        os.environ["NICOS_DB_PATH"] = self.db_file.name
        import importlib
        importlib.reload(db)
        db.run_migrations()
        pairing.start_pairing("operativa", created_by="nicolas", display_name="Setup Worker")
        worker._ultimo_chequeo_recordatorios = None  # forzar que el próximo chequeo corra

    def tearDown(self):
        os.unlink(self.db_file.name)

    @patch("twilio_client.enviar_whatsapp")
    def test_recordatorio_en_ventana_se_manda_y_se_marca_enviado(self, mock_enviar):
        mock_enviar.return_value = {"sid": "SMxxxx", "status": "queued"}
        ahora = datetime.datetime.now()
        turno_dt = ahora + datetime.timedelta(hours=24)
        recordatorio_id = recordatorios.importar_turnos(
            [_turno(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))],
            created_by="nicolas",
        )["ids"][0]

        worker._procesar_recordatorios_si_corresponde()

        mock_enviar.assert_called_once()
        fila = [r for r in recordatorios.list_recordatorios() if r["id"] == recordatorio_id][0]
        self.assertEqual(fila["estado"], "enviado")

    @patch("twilio_client.enviar_whatsapp")
    def test_fallo_de_envio_marca_fallo_envio_no_rompe_el_loop(self, mock_enviar):
        mock_enviar.side_effect = twilio_client.TwilioSendError("Twilio rechazó el envío (400): número inválido")
        ahora = datetime.datetime.now()
        turno_dt = ahora + datetime.timedelta(hours=24)
        recordatorio_id = recordatorios.importar_turnos(
            [_turno(fecha_turno=turno_dt.strftime("%Y-%m-%d"), hora_turno=turno_dt.strftime("%H:%M"))],
            created_by="nicolas",
        )["ids"][0]

        worker._procesar_recordatorios_si_corresponde()  # no debe levantar la excepción hacia afuera

        fila = [r for r in recordatorios.list_recordatorios() if r["id"] == recordatorio_id][0]
        self.assertEqual(fila["estado"], "fallo_envio")


if __name__ == "__main__":
    unittest.main()
