"""
Punto 4 de la revisión post-v0.2.1: la PC Operativa no puede aumentar
permisos aunque tenga un token de dispositivo VÁLIDO -- las rutas
administrativas y de aprobación simplemente no existen para el servidor de
red, sin importar el token. Este test lo prueba con evidencia real (requests
HTTP contra un servidor con is_lan=True y un token de verdad), no solo con
lectura de código.

Usa base de datos temporal -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_operativa_permissions_403.py
"""
import http.client
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

_TMP_DB = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
_TMP_DB.close()
os.environ["NICOS_DB_PATH"] = _TMP_DB.name

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import db  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402
import tasks  # noqa: E402

db.run_migrations()


def _make_server(is_lan: bool):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.is_lan = is_lan
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


class TestOperativaPermissions403(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # Dispositivo real, pareado de verdad -- no un token inventado. Si
        # esto no bastara para pasar las rutas Director-only, confirmaría que
        # el bloqueo es por rol, no por autenticación fallida.
        cls.pairing_result = pairing.complete_pairing(
            pairing.start_pairing()["code"], "PC de prueba"
        )
        cls.token = cls.pairing_result["token"]

        # Tarea real en pending_approval, para que approve/reject/request-info
        # tengan algo válido que intentar tocar (si el bloqueo no funcionara,
        # tiene que fallar por 403, no por 404 "tarea no existe").
        result = tasks.create_task("perm-test-1", "nicolas", None, "Gasté $10.000 en algo")
        cls.task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 10000, "tipo": "gasto"}}
        action_hash = tasks.compute_action_hash(prepared)
        tasks.transition(cls.task_id, "parsing", "system")
        tasks.transition(cls.task_id, "classified", "ai", domain="cfo", intent="register_expense",
                          extracted_json=prepared["args"], risk_level="approval_required", task_revision=1)
        tasks.transition(cls.task_id, "pending_approval", "system", action_version_hash=action_hash,
                          detail={"prepared_action": prepared, "task_revision": 1})
        cls.action_hash = action_hash

        cls.lan_httpd = _make_server(is_lan=True)

    @classmethod
    def tearDownClass(cls):
        cls.lan_httpd.shutdown()
        os.unlink(_TMP_DB.name)

    def _request(self, method, path, body=None, with_token=True):
        conn = http.client.HTTPConnection("127.0.0.1", self.lan_httpd.server_address[1], timeout=5)
        headers = {"Content-Type": "application/json"}
        if with_token:
            headers["Authorization"] = f"Bearer {self.token}"
        payload = json.dumps(body).encode() if body is not None else b""
        conn.request(method, path, body=payload, headers=headers)
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_approve_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("POST", f"/api/v1/tasks/{self.task_id}/approve",
                                      {"approved_action_hash": self.action_hash, "approved_task_revision": 1})
        self.assertEqual(status, 403)
        self.assertFalse(data["ok"])

    def test_reject_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("POST", f"/api/v1/tasks/{self.task_id}/reject", {"reason": "test"})
        self.assertEqual(status, 403)

    def test_request_info_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("POST", f"/api/v1/tasks/{self.task_id}/request-info", {"question": "test"})
        self.assertEqual(status, 403)

    def test_resolve_execution_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("POST", f"/api/v1/tasks/{self.task_id}/resolve-execution",
                                      {"decision": "confirm_executed"})
        self.assertEqual(status, 403)

    def test_pairing_start_bloqueado_por_red_incluso_sin_token(self):
        status, data = self._request("POST", "/api/v1/pairing/start", with_token=False)
        self.assertEqual(status, 403)

    def test_devices_get_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("GET", "/api/v1/devices")
        self.assertEqual(status, 403)

    def test_devices_revoke_bloqueado_por_red_con_token_valido(self):
        status, data = self._request("POST", f"/api/v1/devices/{self.pairing_result['device_id']}/revoke")
        self.assertEqual(status, 403)

    def test_confirmacion_de_que_el_bloqueo_es_por_rol_no_por_auth(self):
        # Contraste: con el MISMO token, crear una tarea propia (ruta que sí
        # le corresponde a Operativa) funciona -- así queda claro que el 403
        # de arriba es por rol, no porque el token esté mal.
        status, data = self._request("POST", "/api/v1/tasks", {"idempotency_key": "perm-test-2", "raw_text": "Otro gasto"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
