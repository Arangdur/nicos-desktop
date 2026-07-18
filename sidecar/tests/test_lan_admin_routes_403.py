"""
Confirma que las rutas administrativas (pairing/start, devices) siguen
devolviendo 403 cuando la request llega por el servidor de RED (LAN/Tailscale)
en vez del servidor LOCAL -- esto ya funcionaba antes de v0.2.1 (Handler._is_lan()
+ chequeos explícitos en server.py), este test solo lo deja como regresión
automatizada en vez de "ya lo probé una vez a mano con curl".

Usa una base de datos SQLite temporal (NICOS_DB_PATH) -- nunca toca nicos.db
real ni ningún archivo de Centro de Mando.

Uso: python3 sidecar/tests/test_lan_admin_routes_403.py
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
import server  # noqa: E402

db.run_migrations()


def _make_server(is_lan: bool):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.is_lan = is_lan
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd, thread


class TestLanAdminRoutes403(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lan_httpd, cls.lan_thread = _make_server(is_lan=True)
        cls.local_httpd, cls.local_thread = _make_server(is_lan=False)

    @classmethod
    def tearDownClass(cls):
        cls.lan_httpd.shutdown()
        cls.local_httpd.shutdown()
        os.unlink(_TMP_DB.name)

    def _request(self, httpd, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        payload = json.dumps(body).encode() if body is not None else b""
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_pairing_start_bloqueado_por_lan(self):
        status, data = self._request(self.lan_httpd, "POST", "/api/v1/pairing/start")
        self.assertEqual(status, 403)
        self.assertFalse(data["ok"])

    def test_devices_bloqueado_por_lan(self):
        status, data = self._request(self.lan_httpd, "GET", "/api/v1/devices")
        self.assertEqual(status, 403)
        self.assertFalse(data["ok"])

    def test_pairing_start_funciona_local(self):
        status, data = self._request(self.local_httpd, "POST", "/api/v1/pairing/start")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])
        self.assertIn("code", data)

    def test_devices_funciona_local(self):
        status, data = self._request(self.local_httpd, "GET", "/api/v1/devices")
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])


if __name__ == "__main__":
    unittest.main()
