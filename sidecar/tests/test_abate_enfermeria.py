"""
Módulo real de Enfermería de Abate -- prueba con evidencia real (requests
HTTP contra servidores con is_lan=True/False y tokens de verdad, no solo
lectura de código) que:

1. Dar de alta residentes y fijar el tratamiento vigente es Director-only
   (bloqueado a nivel de servidor, igual que las demás rutas admin).
2. Registrar administración de medicación y cargar novedades es
   Enfermero-only -- Operativa recibe 403 aunque tenga un token válido.
3. Las novedades se guardan tal cual las escribe el enfermero (sin pasar por
   ai_router.py).
4. Continuidad de turno: un enfermero ve las novedades cargadas por otro,
   trazadas a quién las escribió.
5. Una toma de medicación no se puede registrar dos veces (idempotencia) ni
   contra una droga/horario que no está en el tratamiento vigente.

Usa base de datos temporal -- nunca toca nicos.db real.

Uso: python3 sidecar/tests/test_abate_enfermeria.py
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

import abate_enfermeria  # noqa: E402
import db  # noqa: E402
import pairing  # noqa: E402
import server  # noqa: E402

db.run_migrations()


def _make_server(is_lan: bool):
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
    httpd.is_lan = is_lan
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    return httpd


def _pair(role, display_name, turno=None):
    code = pairing.start_pairing(role, turno=turno, created_by="nicolas", display_name=display_name)["code"]
    return pairing.complete_pairing(
        code, f"PC de {display_name}",
        display_name=display_name, dni="30" + str(abs(hash(display_name)))[:6],
        fecha_nacimiento="1990-01-01", sexo="NC", pin="1234",
    )


class TestAbateEnfermeria(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.local_httpd = _make_server(is_lan=False)   # simula al Director en su propia Mac
        cls.lan_httpd = _make_server(is_lan=True)       # simula la red de Tailscale

        cls.enfermero_a = _pair("enfermero", "Carlos Turno Mañana", turno="manana")
        cls.enfermero_b = _pair("enfermero", "María Turno Noche", turno="noche")
        cls.operativa = _pair("operativa", "Marianela Prueba")

        # Residente + tratamiento reales, creados como Director (local).
        status, data = cls._request_static(cls.local_httpd, "POST", "/api/v1/abate/residentes", {"nombre": "Residente Demo"})
        assert status == 200, data
        cls.residente_id = data["residente_id"]

        status, data = cls._request_static(
            cls.local_httpd, "POST", f"/api/v1/abate/residentes/{cls.residente_id}/tratamiento",
            {"medicacion": [{"droga": "Clonazepam", "dosis": "0.5mg", "horarios": ["08:00", "20:00"]}], "indicaciones": "Con el desayuno"},
        )
        assert status == 200, data

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

    # 1. Director-only ------------------------------------------------------

    def test_director_crea_residente_ok(self):
        status, data = self._local("POST", "/api/v1/abate/residentes", {"nombre": "Otro Residente"})
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_enfermero_no_puede_crear_residente_por_red(self):
        status, data = self._as(self.enfermero_a, "POST", "/api/v1/abate/residentes", {"nombre": "Colado"})
        self.assertEqual(status, 403)

    def test_enfermero_no_puede_fijar_tratamiento_por_red(self):
        status, data = self._as(
            self.enfermero_a, "POST", f"/api/v1/abate/residentes/{self.residente_id}/tratamiento",
            {"medicacion": [{"droga": "Lo que sea", "dosis": "1", "horarios": ["10:00"]}]},
        )
        self.assertEqual(status, 403)

    # 2. Enfermero-only -------------------------------------------------------

    def test_enfermero_puede_registrar_administracion(self):
        # unittest corre los tests en orden alfabético, no en el orden en que
        # están escritos acá -- cada test de este archivo usa su PROPIO
        # horario para no pisarse con otro que también registre una toma
        # sobre el mismo residente (ver test_administracion_duplicada, que
        # necesita registrar dos veces a propósito).
        status, data = self._as(
            self.enfermero_a, "POST", f"/api/v1/abate/residentes/{self.residente_id}/administraciones",
            {"droga": "Clonazepam", "horario_previsto": "08:00"},
        )
        self.assertEqual(status, 200)
        self.assertTrue(data["ok"])

    def test_operativa_no_puede_registrar_administracion(self):
        status, data = self._as(
            self.operativa, "POST", f"/api/v1/abate/residentes/{self.residente_id}/administraciones",
            {"droga": "Clonazepam", "horario_previsto": "20:00"},
        )
        self.assertEqual(status, 403)

    def test_administracion_duplicada_es_rechazada(self):
        # Self-contained: registra una vez (debe funcionar) y de nuevo
        # inmediatamente (debe rechazarse) -- no depende de que otro test
        # haya corrido antes.
        status, data = self._as(
            self.enfermero_b, "POST", f"/api/v1/abate/residentes/{self.residente_id}/administraciones",
            {"droga": "Clonazepam", "horario_previsto": "20:00"},
        )
        self.assertEqual(status, 200, data)
        status, data = self._as(
            self.enfermero_b, "POST", f"/api/v1/abate/residentes/{self.residente_id}/administraciones",
            {"droga": "Clonazepam", "horario_previsto": "20:00"},
        )
        self.assertEqual(status, 400)

    def test_administracion_fuera_del_tratamiento_vigente_es_rechazada(self):
        status, data = self._as(
            self.enfermero_a, "POST", f"/api/v1/abate/residentes/{self.residente_id}/administraciones",
            {"droga": "Droga Inventada", "horario_previsto": "08:00"},
        )
        self.assertEqual(status, 400)

    def test_enfermero_puede_cargar_novedad(self):
        status, data = self._as(
            self.enfermero_a, "POST", "/api/v1/abate/novedades",
            {"residente_id": self.residente_id, "categoria": "operativo", "texto": "Desayunó bien."},
        )
        self.assertEqual(status, 200)

    def test_operativa_no_puede_cargar_novedad(self):
        status, data = self._as(
            self.operativa, "POST", "/api/v1/abate/novedades",
            {"residente_id": self.residente_id, "categoria": "operativo", "texto": "No debería poder."},
        )
        self.assertEqual(status, 403)

    def test_novedad_categoria_invalida_rechazada(self):
        status, data = self._as(
            self.enfermero_a, "POST", "/api/v1/abate/novedades",
            {"residente_id": self.residente_id, "categoria": "no-existe", "texto": "Texto."},
        )
        self.assertEqual(status, 400)

    # 3. Texto tal cual, sin IA -----------------------------------------------

    def test_novedad_se_guarda_tal_cual_sin_transformar(self):
        texto_original = "  Durmió mal, se despertó 3 veces.  "
        status, data = self._as(
            self.enfermero_b, "POST", "/api/v1/abate/novedades",
            {"residente_id": self.residente_id, "categoria": "clinico_conductual", "texto": texto_original},
        )
        self.assertEqual(status, 200)
        guardada = abate_enfermeria.list_novedades(residente_id=self.residente_id, categoria="clinico_conductual")[0]
        # .strip() es la única transformación permitida (espacios al borde) --
        # el contenido en sí nunca pasa por ai_router.
        self.assertEqual(guardada["texto"], texto_original.strip())

    # 4. Continuidad de turno ---------------------------------------------------

    def test_continuidad_de_turno_un_enfermero_ve_lo_de_otro(self):
        # Self-contained: usa un residente propio (no el compartido con otros
        # tests) para no depender de qué otros tests ya corrieron antes.
        status, data = self._local("POST", "/api/v1/abate/residentes", {"nombre": "Residente Continuidad"})
        residente_id = data["residente_id"]

        status, _ = self._as(
            self.enfermero_a, "POST", "/api/v1/abate/novedades",
            {"residente_id": residente_id, "categoria": "operativo", "texto": "Carlos: todo bien en la mañana."},
        )
        self.assertEqual(status, 200)
        status, _ = self._as(
            self.enfermero_b, "POST", "/api/v1/abate/novedades",
            {"residente_id": residente_id, "categoria": "operativo", "texto": "María: tranquilo en la noche."},
        )
        self.assertEqual(status, 200)

        status, data = self._as(self.enfermero_a, "GET", f"/api/v1/abate/novedades?residente_id={residente_id}")
        self.assertEqual(status, 200)
        autores = {n["created_by_nombre"] for n in data["novedades"]}
        # Carlos (enfermero_a) tiene que poder ver la novedad que cargó María
        # (enfermero_b) del mismo residente -- continuidad de turno real.
        self.assertIn("María Turno Noche", autores)
        self.assertIn("Carlos Turno Mañana", autores)


if __name__ == "__main__":
    unittest.main()
