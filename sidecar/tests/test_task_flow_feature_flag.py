"""
Confirma el mecanismo de rollback real (v0.2.1): con NICOS_TASK_FLOW_ENABLED=false,
/api/v1/tasks* devuelve 503 sin tocar el resto del sidecar. TASK_FLOW_ENABLED se lee
una sola vez al importar server.py, así que este test fuerza una recarga limpia del
módulo con la variable ya seteada -- no alcanza con parchear el atributo después.

Usa una base de datos SQLite temporal (NICOS_DB_PATH) -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_task_flow_feature_flag.py
"""
import http.client
import importlib
import json
import os
import sys
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_server_module(task_flow_enabled: bool):
    """Reimporta server.py (y db.py) desde cero con las env vars ya seteadas --
    TASK_FLOW_ENABLED y DB_PATH son constantes de módulo, leídas una sola vez."""
    tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
    tmp_db.close()
    os.environ["NICOS_DB_PATH"] = tmp_db.name
    os.environ["NICOS_TASK_FLOW_ENABLED"] = "true" if task_flow_enabled else "false"

    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing"):
        sys.modules.pop(mod, None)

    import db as db_mod
    db_mod.run_migrations()
    server_mod = importlib.import_module("server")
    return server_mod, tmp_db.name


class TestTaskFlowFeatureFlag(unittest.TestCase):
    def _request(self, httpd, method, path, body=None):
        conn = http.client.HTTPConnection("127.0.0.1", httpd.server_address[1], timeout=5)
        payload = json.dumps(body).encode() if body is not None else b""
        conn.request(method, path, body=payload, headers={"Content-Type": "application/json"})
        resp = conn.getresponse()
        data = json.loads(resp.read())
        conn.close()
        return resp.status, data

    def test_flag_false_bloquea_tasks_pero_no_el_resto(self):
        server_mod, tmp_db_path = _fresh_server_module(task_flow_enabled=False)
        self.assertFalse(server_mod.TASK_FLOW_ENABLED)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        httpd.is_lan = False
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            status, data = self._request(httpd, "GET", "/api/v1/tasks")
            self.assertEqual(status, 503)
            self.assertFalse(data["ok"])

            status, data = self._request(httpd, "POST", "/api/v1/tasks", {"idempotency_key": "x", "raw_text": "y"})
            self.assertEqual(status, 503)

            # Lo que ya andaba en v0.1 sigue andando igual.
            status, data = self._request(httpd, "GET", "/ping")
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
        finally:
            httpd.shutdown()
            os.unlink(tmp_db_path)

    def test_flag_true_default_permite_tasks(self):
        server_mod, tmp_db_path = _fresh_server_module(task_flow_enabled=True)
        self.assertTrue(server_mod.TASK_FLOW_ENABLED)

        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server_mod.Handler)
        httpd.is_lan = False
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            status, data = self._request(httpd, "GET", "/api/v1/tasks")
            self.assertEqual(status, 200)
            self.assertTrue(data["ok"])
        finally:
            httpd.shutdown()
            os.unlink(tmp_db_path)


if __name__ == "__main__":
    unittest.main()
