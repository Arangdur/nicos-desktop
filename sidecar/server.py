#!/usr/bin/env python3
"""
Sidecar local de NicOS Desktop — mismo patrón que trading_bot/jarvis-trabajo/jarvis-server.py
(http.server puro, sin frameworks, rutas manuales).

Corre DOS servidores HTTP en el mismo proceso:
  - servidor LOCAL, bind 127.0.0.1, puerto elegido por el SO (igual que v0.1) — lo usa
    la propia app Electron del Director (Nicolás), en la misma máquina. Confiado por
    definición (solo un proceso local puede llegar a 127.0.0.1) — sin verificación de token.
  - servidor LAN, bind 0.0.0.0, puerto FIJO (NICOS_LAN_PORT) — lo usa la app Electron
    de la vista Operativa (Marianela) desde la PC Windows, por la red local. Todas las
    rutas de tareas requieren `Authorization: Bearer <token>` emitido por el pairing
    (ver pairing.py); nunca acepta credenciales de IA/Google — esas viven solo en la Mac.

Rutas nuevas de v0.2 bajo /api/v1/* (flujo de tareas con aprobación). Las rutas de v0.1
(/ping, /whatsapp/*, /director/*) siguen igual, solo servidas por el servidor LOCAL —
el chat y el editor directo del Sheet no se exponen a la LAN por diseño (siguen siendo
funciones del Director en su propia máquina).
"""
import json
import os
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import ai_router
import centro_mando_adapter
import db
import pairing
import sheets_client
import tasks

LAN_PORT = int(os.getenv("NICOS_LAN_PORT", "47500"))

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


def _process_task_async(task_id: str):
    """Corre en un thread aparte: parsing -> classified -> (needs_information |
    pending_approval | ready) -> si es 'ready', ejecuta. Nunca bloquea la respuesta
    HTTP de creación de la tarea."""
    try:
        task = tasks.get_task_dict(task_id)
        tasks.transition(task_id, "parsing", "system")

        extraction = ai_router.extract(task["raw_text"])
        if not extraction.get("ok"):
            # falla técnica de extracción (ej. sin API keys configuradas) -> 'failed',
            # no 'needs_review' (ese estado es para cuando SÍ se clasificó pero no se
            # puede automatizar, ej. dominio abate — ver más abajo).
            tasks.transition(
                task_id, "failed", "system",
                detail={"motivo": "extracción falló", "error": extraction.get("error")},
                error_message=extraction.get("error"),
            )
            return

        extracted = extraction["data"]
        domain = centro_mando_adapter.classify_request(extracted)
        if domain is None:
            tasks.transition(
                task_id, "needs_information", "system",
                detail={"pregunta": "No pude determinar si esto es de CFO o de Abate — ¿podés aclararlo?"},
                domain=extracted.get("domain"), intent=extracted.get("intent"), extracted_json=extracted,
            )
            return

        risk = centro_mando_adapter.evaluate_risk(domain, extracted.get("intent"), extracted)
        tasks.transition(
            task_id, "classified", "ai",
            detail={"extraction_provider": extraction.get("provider")},
            domain=domain, intent=extracted.get("intent"), extracted_json=extracted, risk_level=risk,
        )

        prepared = centro_mando_adapter.prepare_action(domain, extracted.get("intent"), extracted)
        action_hash = tasks.compute_action_hash(prepared)

        if risk == "simple":
            tasks.transition(task_id, "ready", "system", action_version_hash=action_hash,
                              detail={"prepared_action": prepared})
        else:
            tasks.transition(task_id, "pending_approval", "system", action_version_hash=action_hash,
                              detail={"prepared_action": prepared})
            return  # espera aprobación humana — no se ejecuta acá

        _execute_ready_task(task_id, prepared)
    except Exception as e:
        sys.stderr.write("[sidecar] ERROR procesando tarea: " + traceback.format_exc() + "\n")
        try:
            tasks.transition(task_id, "needs_review", "system", detail={"error": str(e)})
        except Exception:
            pass


def _execute_ready_task(task_id: str, prepared_action: dict):
    tasks.transition(task_id, "executing", "system")
    result = centro_mando_adapter.execute_action(prepared_action)
    centro_mando_adapter.record_result(task_id, "system", result)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, fmt, *args):
        sys.stderr.write("[sidecar] " + (fmt % args) + "\n")

    def send_cors(self):
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")

    def send_no_cache(self):
        self.send_header("Cache-Control", "no-store, no-cache, must-revalidate, max-age=0")

    def _send_json(self, status, payload):
        body = json.dumps(payload, ensure_ascii=False, default=str).encode("utf-8")
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

    def _is_lan(self):
        return getattr(self.server, "is_lan", False)

    def _authenticate(self):
        """Servidor local: confiado, siempre 'nicolas'. Servidor LAN: exige Bearer
        token válido emitido por pairing — devuelve None si falta o es inválido."""
        if not self._is_lan():
            return {"user_id": "nicolas", "device_id": None}
        auth = self.headers.get("Authorization", "")
        if not auth.startswith("Bearer "):
            return None
        token = auth[len("Bearer "):].strip()
        device = pairing.verify_token(token)
        if device is None:
            return None
        return {"user_id": device["user_id"], "device_id": device["device_id"]}

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
                return

            if self._is_lan() and path in ("/whatsapp/messages", "/director/summary"):
                self._send_json(403, {"ok": False, "error": "ruta no disponible en la red local"})
                return

            if path == "/whatsapp/messages":
                messages = sheets_client.list_messages()
                self._send_json(200, {"ok": True, "messages": messages})
            elif path == "/director/summary":
                self._send_json(200, {"ok": True, "summary": _director_summary()})
            elif path == "/api/v1/tasks":
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                state = None
                if parsed.query:
                    from urllib.parse import parse_qs
                    qs = parse_qs(parsed.query)
                    state = qs.get("state", [None])[0]
                self._send_json(200, {"ok": True, "tasks": tasks.list_tasks(state=state)})
            elif path.startswith("/api/v1/tasks/") and path.count("/") == 4:
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                task_id = path.rsplit("/", 1)[-1]
                task = tasks.get_task_dict(task_id)
                if task is None:
                    self._send_json(404, {"ok": False, "error": "tarea no encontrada"})
                    return
                events = tasks.get_task_events(task_id)
                self._send_json(200, {"ok": True, "task": task, "events": events})
            elif path == "/api/v1/whatsapp/messages":
                # Versión autenticada de /whatsapp/messages, alcanzable desde la LAN
                # (Marianela) con su token de dispositivo — las credenciales de Google
                # siguen viviendo solo acá en la Mac, nunca en su PC.
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                messages = sheets_client.list_messages()
                self._send_json(200, {"ok": True, "messages": messages})
            elif path == "/api/v1/devices":
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                self._send_json(200, {"ok": True, "devices": pairing.list_devices()})
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
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "ruta no disponible en la red local"})
                    return
                row = body.get("row")
                updates = body.get("updates", {})
                if not row or not isinstance(updates, dict):
                    self._send_json(400, {"ok": False, "error": "body requiere {row, updates}"})
                    return
                result = sheets_client.update_message(row, updates)
                self._send_json(200, {"ok": True, **result})

            elif path == "/director/chat":
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "ruta no disponible en la red local"})
                    return
                question = body.get("message", "")
                brain = body.get("brain", "claude")
                history = body.get("history", [])
                context = _director_summary()
                result = ai_router.ask_director(question, context, history, brain)
                self._send_json(200, result)

            elif path == "/api/v1/whatsapp/messages/update":
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                row = body.get("row")
                updates = body.get("updates", {})
                if not row or not isinstance(updates, dict):
                    self._send_json(400, {"ok": False, "error": "body requiere {row, updates}"})
                    return
                result = sheets_client.update_message(row, updates)
                self._send_json(200, {"ok": True, **result})

            elif path == "/api/v1/pairing/start":
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "el pairing se inicia desde la Mac"})
                    return
                self._send_json(200, {"ok": True, **pairing.start_pairing()})

            elif path == "/api/v1/pairing/complete":
                code = body.get("code", "")
                device_name = body.get("device_name", "Dispositivo sin nombre")
                try:
                    result = pairing.complete_pairing(code, device_name)
                    self._send_json(200, {"ok": True, **result})
                except pairing.PairingError as e:
                    self._send_json(400, {"ok": False, "error": str(e)})

            elif path == "/api/v1/tasks":
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                idempotency_key = body.get("idempotency_key")
                raw_text = body.get("raw_text", "").strip()
                if not idempotency_key or not raw_text:
                    self._send_json(400, {"ok": False, "error": "body requiere {idempotency_key, raw_text}"})
                    return
                result = tasks.create_task(
                    idempotency_key, auth["user_id"], auth["device_id"], raw_text,
                    attachment_path=body.get("attachment_path"),
                )
                if result["created"]:
                    threading.Thread(
                        target=_process_task_async, args=(result["task"]["task_id"],), daemon=True
                    ).start()
                self._send_json(200, {"ok": True, **result})

            elif path.startswith("/api/v1/tasks/") and path.endswith("/approve"):
                self._handle_task_action(path, body, "approve")
            elif path.startswith("/api/v1/tasks/") and path.endswith("/reject"):
                self._handle_task_action(path, body, "reject")
            elif path.startswith("/api/v1/tasks/") and path.endswith("/request-info"):
                self._handle_task_action(path, body, "request-info")

            elif path.startswith("/api/v1/devices/") and path.endswith("/revoke"):
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                device_id = path.split("/")[3]
                pairing.revoke_device(device_id)
                self._send_json(200, {"ok": True})

            else:
                self._send_json(404, {"ok": False, "error": "ruta no encontrada"})
        except sheets_client.SheetsConfigError as e:
            self._send_json(200, {"ok": False, "error": str(e)})
        except json.JSONDecodeError:
            self._send_json(400, {"ok": False, "error": "body no es JSON válido"})
        except Exception as e:
            sys.stderr.write("[sidecar] ERROR: " + traceback.format_exc() + "\n")
            self._send_json(500, {"ok": False, "error": str(e)})

    def _handle_task_action(self, path, body, action):
        if self._is_lan():
            self._send_json(403, {"ok": False, "error": "las aprobaciones se hacen desde la Mac"})
            return
        auth = self._authenticate()
        task_id = path.split("/")[3]
        try:
            if action == "approve":
                result = tasks.approve_task(task_id, auth["user_id"], body.get("approved_action_hash", ""))
                # tras aprobar queda en 'ready' -> ejecutar ahora, en background
                threading.Thread(target=self._execute_after_approval, args=(task_id,), daemon=True).start()
                self._send_json(200, {"ok": True, "task": result})
            elif action == "reject":
                result = tasks.reject_task(task_id, auth["user_id"], body.get("reason", ""))
                self._send_json(200, {"ok": True, "task": result})
            elif action == "request-info":
                result = tasks.request_info(task_id, auth["user_id"], body.get("question", ""))
                self._send_json(200, {"ok": True, "task": result})
        except tasks.StaleApproval as e:
            self._send_json(409, {"ok": False, "error": str(e)})
        except tasks.InvalidTransition as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._send_json(404, {"ok": False, "error": str(e)})

    def _execute_after_approval(self, task_id):
        task = tasks.get_task_dict(task_id)
        events = tasks.get_task_events(task_id)
        prepared = None
        for e in reversed(events):
            detail = e.get("detail_json")
            if isinstance(detail, str):
                try:
                    detail = json.loads(detail)
                except json.JSONDecodeError:
                    detail = {}
            if detail and "prepared_action" in detail:
                prepared = detail["prepared_action"]
                break
        if prepared is None:
            tasks.transition(task_id, "needs_review", "system",
                              detail={"error": "no se encontró la acción preparada en el historial"})
            return
        _execute_ready_task(task_id, prepared)


def _serve(host, port, is_lan, ready_callback=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.is_lan = is_lan
    actual_port = server.server_address[1]
    if ready_callback:
        ready_callback(actual_port)
    server.serve_forever()


def main():
    db.run_migrations()

    local_port_holder = {}

    def _on_local_ready(port):
        local_port_holder["port"] = port
        # Primera línea de stdout: Electron la lee para saber a qué puerto local conectarse.
        print(f"NICOS_SIDECAR_PORT={port}", flush=True)
        sys.stderr.write(f"[sidecar] servidor LOCAL escuchando en http://127.0.0.1:{port}\n")

    lan_thread = threading.Thread(
        target=_serve, args=("0.0.0.0", LAN_PORT, True), daemon=True
    )
    lan_thread.start()
    sys.stderr.write(f"[sidecar] servidor LAN escuchando en 0.0.0.0:{LAN_PORT}\n")

    try:
        _serve("127.0.0.1", 0, False, ready_callback=_on_local_ready)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
