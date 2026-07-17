#!/usr/bin/env python3
"""
Sidecar local de NicOS Desktop — mismo patrón que trading_bot/jarvis-trabajo/jarvis-server.py
(http.server puro, sin frameworks, rutas manuales), extendido con:
  - /whatsapp/*   -> vista Operativa (lee/escribe la hoja "Bot WhatsApp Consultorio")
  - /director/*   -> vista Director (lee resúmenes locales del ecosistema + chat Claude/OpenAI)

A diferencia de jarvis-server.py (que usa Access-Control-Allow-Origin: "null" a propósito,
porque lo puede pedir cualquier pestaña http://localhost:7070), este sidecar solo lo consume
el propio proceso de Electron de esta app empaquetada — no hay otro cliente de confianza
esperado en localhost, así que el CORS acá es más permisivo (ver send_cors) sin ser el
mismo caso de uso.

Arranca en 127.0.0.1 con puerto 0 (el SO elige uno libre) para no repetir el problema ya
conocido de puertos ocupados (AirPlay Receiver en 5000, etc.) — imprime el puerto elegido
por stdout como primera línea, en formato "NICOS_SIDECAR_PORT=<puerto>", para que el
proceso principal de Electron lo lea y lo use al armar las URLs del sidecar.
"""
import json
import os
import sys
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse, parse_qs

import ai_router
import sheets_client

# Carpeta donde viven los JSON de resumen del ecosistema (solo existen en la Mac de Nicolás).
JARVIS_TRABAJO_PATH = os.getenv(
    "JARVIS_TRABAJO_PATH",
    "/Users/nicolasbuso/trading_bot/jarvis-trabajo",
)

SUMMARY_FILES = {
    "trading_bot": "trading-bot-resumen.json",
    "consultorio": "consultorio-resumen.json",
    "cowork": "cowork-snapshot.json",
    "cfo_vivo": "cfo-vivo-resumen.json",
}


def _read_json_safe(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except FileNotFoundError:
        return None
    except json.JSONDecodeError as e:
        return {"_error": f"JSON inválido en {os.path.basename(path)}: {e}"}


def _director_summary():
    summary = {}
    for key, filename in SUMMARY_FILES.items():
        path = os.path.join(JARVIS_TRABAJO_PATH, filename)
        data = _read_json_safe(path)
        summary[key] = data if data is not None else {"_missing": True, "archivo": filename}
    return summary


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[sidecar] " + (fmt % args) + "\n")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def send_no_cache(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_cors()
        self.send_no_cache()
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def _read_json_body(self):
        length = int(self.headers.get("Content-Length", 0))
        if length == 0:
            return {}
        raw = self.rfile.read(length)
        return json.loads(raw.decode("utf-8"))

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_cors()
        self.end_headers()

    def do_GET(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            if path == "/ping":
                self._send_json(200, {"ok": True})
            elif path == "/whatsapp/messages":
                messages = sheets_client.list_messages()
                self._send_json(200, {"ok": True, "messages": messages})
            elif path == "/director/summary":
                self._send_json(200, {"ok": True, "summary": _director_summary()})
            else:
                self._send_json(404, {"ok": False, "error": "ruta no encontrada"})
        except sheets_client.SheetsConfigError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
        except Exception as e:
            sys.stderr.write("[sidecar] ERROR: " + traceback.format_exc() + "\n")
            self._send_json(500, {"ok": False, "error": str(e)})

    def do_POST(self):
        parsed = urlparse(self.path)
        path = parsed.path
        try:
            body = self._read_json_body()
            if path == "/whatsapp/messages/update":
                row = body.get("row")
                updates = body.get("updates", {})
                if not row or not isinstance(updates, dict):
                    self._send_json(400, {"ok": False, "error": "body requiere {row, updates}"})
                    return
                result = sheets_client.update_message(row, updates)
                self._send_json(200, {"ok": True, **result})
            elif path == "/director/chat":
                question = body.get("message", "")
                brain = body.get("brain", "claude")
                history = body.get("history", [])
                context = _director_summary()
                result = ai_router.ask_director(question, context, history, brain)
                self._send_json(200, result)
            else:
                self._send_json(404, {"ok": False, "error": "ruta no encontrada"})
        except sheets_client.SheetsConfigError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "body no es JSON válido"})
        except Exception as e:
            sys.stderr.write("[sidecar] ERROR: " + traceback.format_exc() + "\n")
            self._send_json(500, {"ok": False, "error": str(e)})


def main():
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    port = server.server_address[1]
    # Primera línea de stdout: el proceso de Electron la lee para saber a qué puerto conectarse.
    print(f"NICOS_SIDECAR_PORT={port}", flush=True)
    sys.stderr.write(f"[sidecar] escuchando en http://127.0.0.1:{port}\n")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":
    main()
