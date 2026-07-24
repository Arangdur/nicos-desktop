"""
Cliente de la API de DrApp -- prueba con mocks (nunca pega a la red real) que:

1. Sin DRAPP_API_KEY/DRAPP_TEAM_ID configurados, cada función falla claro con
   DrAppConfigError en vez de un error críptico de conexión.
2. `crear_turno` propaga un 409 como DrAppConflictError específicamente (no un
   error genérico) -- quien llama tiene que poder distinguir "el slot se
   ocupó" de cualquier otro fallo.
3. `list_turnos_medicina_general` filtra los turnos de Psiquiatría por
   `service.label`, nunca los incluye.
4. `get_telefono_paciente` y `buscar_paciente_por_telefono` nunca inventan un
   dato -- devuelven None si no hay nada cargado.
5. Las requests reales que se arman (URL, método, headers) coinciden con lo
   documentado en el OpenAPI de DrApp.

Uso: python3 sidecar/tests/test_drapp_client.py
"""
import io
import json
import os
import sys
import unittest
import urllib.error
from unittest.mock import patch, MagicMock

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import drapp_client  # noqa: E402


def _fake_response(payload):
    body = json.dumps(payload).encode("utf-8") if payload is not None else b""
    cm = MagicMock()
    cm.read.return_value = body
    cm.__enter__.return_value = cm
    cm.__exit__.return_value = False
    return cm


def _fake_http_error(status, error_code, message):
    body = json.dumps({"error": error_code, "message": message, "status": status, "request_id": "req-1"}).encode()
    return urllib.error.HTTPError(
        url="https://api.drapp.com.ar/public/v1/x", code=status, msg=error_code,
        hdrs=None, fp=io.BytesIO(body),
    )


class TestConfig(unittest.TestCase):
    def test_sin_credenciales_falla_claro(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(drapp_client.DrAppConfigError):
                drapp_client.consultar_disponibilidad("resources/dr-buso", "consulta-general", "2026-08-01", "2026-08-07")

    def test_falta_solo_team_id_tambien_falla(self):
        with patch.dict(os.environ, {"DRAPP_API_KEY": "drapp_sandbox_x"}, clear=True):
            with self.assertRaises(drapp_client.DrAppConfigError):
                drapp_client.cancelar_turno("events/abc123")


class TestDisponibilidadYTurnos(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DRAPP_API_KEY": "drapp_sandbox_x", "DRAPP_TEAM_ID": "clinica-norte"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    @patch("drapp_client.urllib.request.urlopen")
    def test_consultar_disponibilidad_arma_la_url_correcta(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response([{"date": "2026-08-01", "slots": []}])
        drapp_client.consultar_disponibilidad("resources/dr-buso", "consulta-general", "2026-08-01", "2026-08-07")
        request = mock_urlopen.call_args[0][0]
        self.assertIn("/teams/clinica-norte/availability", request.full_url)
        self.assertIn("resource=resources%2Fdr-buso", request.full_url)
        self.assertEqual(request.get_header("Authorization"), "Bearer drapp_sandbox_x")

    @patch("drapp_client.urllib.request.urlopen")
    def test_crear_turno_ok(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "id": "events/nuevo123", "status": "booked", "day": "2026-08-10", "time": "09:00",
        })
        resultado = drapp_client.crear_turno("resources/dr-buso", "consulta-general", "consumers/xyz789", "2026-08-10", "09:00")
        self.assertEqual(resultado["id"], "events/nuevo123")
        request = mock_urlopen.call_args[0][0]
        self.assertEqual(request.get_method(), "POST")
        body = json.loads(request.data)
        self.assertEqual(body["consumer"]["id"], "consumers/xyz789")
        # Idempotency-Key siempre va, aunque quien llama no pase una --
        # protege contra duplicar el turno si el caller reintenta.
        self.assertTrue(request.get_header("Idempotency-key"))

    @patch("drapp_client.urllib.request.urlopen")
    def test_crear_turno_reintento_usa_la_misma_idempotency_key(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"id": "events/nuevo123", "status": "booked"})
        drapp_client.crear_turno(
            "resources/dr-buso", "consulta-general", "consumers/xyz789", "2026-08-10", "09:00",
            idempotency_key="reintento-fijo-123",
        )
        drapp_client.crear_turno(
            "resources/dr-buso", "consulta-general", "consumers/xyz789", "2026-08-10", "09:00",
            idempotency_key="reintento-fijo-123",
        )
        primera = mock_urlopen.call_args_list[0][0][0].get_header("Idempotency-key")
        segunda = mock_urlopen.call_args_list[1][0][0].get_header("Idempotency-key")
        self.assertEqual(primera, "reintento-fijo-123")
        self.assertEqual(primera, segunda)

    @patch("drapp_client.urllib.request.urlopen")
    def test_crear_turno_conflicto_levanta_error_especifico(self, mock_urlopen):
        mock_urlopen.side_effect = _fake_http_error(409, "conflict", "Slot ya ocupado")
        with self.assertRaises(drapp_client.DrAppConflictError):
            drapp_client.crear_turno("resources/dr-buso", "consulta-general", "consumers/xyz789", "2026-08-10", "09:00")

    @patch("drapp_client.urllib.request.urlopen")
    def test_crear_turno_otro_error_no_es_conflicto(self, mock_urlopen):
        mock_urlopen.side_effect = _fake_http_error(400, "bad_request", "Falta service.key")
        with self.assertRaises(drapp_client.DrAppAPIError) as ctx:
            drapp_client.crear_turno("resources/dr-buso", "consulta-general", "consumers/xyz789", "2026-08-10", "09:00")
        self.assertNotIsInstance(ctx.exception, drapp_client.DrAppConflictError)

    @patch("drapp_client.urllib.request.urlopen")
    def test_cancelar_turno_saca_el_prefijo_events(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"id": "events/abc123", "status": "cancelled"})
        drapp_client.cancelar_turno("events/abc123")
        request = mock_urlopen.call_args[0][0]
        self.assertTrue(request.full_url.endswith("/teams/clinica-norte/events/abc123"))
        self.assertEqual(request.get_method(), "DELETE")

    @patch("drapp_client.urllib.request.urlopen")
    def test_reprogramar_turno_manda_day_y_time(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"id": "events/abc123", "day": "2026-08-15", "time": "11:00"})
        drapp_client.reprogramar_turno("events/abc123", "2026-08-15", "11:00")
        request = mock_urlopen.call_args[0][0]
        body = json.loads(request.data)
        self.assertEqual(body, {"day": "2026-08-15", "time": "11:00"})

    @patch("drapp_client.urllib.request.urlopen")
    def test_list_turnos_medicina_general_excluye_psiquiatria(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response([
            {"id": "events/1", "service": {"label": "Medicina General"}, "day": "2026-08-01", "time": "10:00"},
            {"id": "events/2", "service": {"label": "Consulta Psiquiatría"}, "day": "2026-08-01", "time": "15:30"},
            {"id": "events/3", "service": {"label": "Psiquiatría / Consulta"}, "day": "2026-08-02", "time": "08:00"},
        ])
        turnos = drapp_client.list_turnos_medicina_general("2026-08-01", "2026-08-07")
        self.assertEqual(len(turnos), 1)
        self.assertEqual(turnos[0]["id"], "events/1")


class TestPacientes(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {"DRAPP_API_KEY": "drapp_sandbox_x", "DRAPP_TEAM_ID": "clinica-norte"})
        self.env_patch.start()

    def tearDown(self):
        self.env_patch.stop()

    @patch("drapp_client.urllib.request.urlopen")
    def test_get_telefono_paciente_devuelve_el_primero(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({
            "id": "consumers/xyz789", "phones": [{"value": "+5491155678901", "label": "celular"}],
        })
        telefono = drapp_client.get_telefono_paciente("consumers/xyz789")
        self.assertEqual(telefono, "+5491155678901")

    @patch("drapp_client.urllib.request.urlopen")
    def test_get_telefono_paciente_sin_telefonos_devuelve_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"id": "consumers/xyz789", "phones": []})
        telefono = drapp_client.get_telefono_paciente("consumers/xyz789")
        self.assertIsNone(telefono)

    @patch("drapp_client.urllib.request.urlopen")
    def test_buscar_paciente_por_telefono_sin_match_devuelve_none(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"data": []})
        paciente = drapp_client.buscar_paciente_por_telefono("+5493537000000")
        self.assertIsNone(paciente)

    @patch("drapp_client.urllib.request.urlopen")
    def test_buscar_paciente_por_telefono_con_match(self, mock_urlopen):
        mock_urlopen.return_value = _fake_response({"data": [{"id": "consumers/xyz789", "firstName": "María"}]})
        paciente = drapp_client.buscar_paciente_por_telefono("+5491155678901")
        self.assertEqual(paciente["id"], "consumers/xyz789")


if __name__ == "__main__":
    unittest.main()
