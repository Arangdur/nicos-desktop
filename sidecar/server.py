#!/usr/bin/env python3
"""
Sidecar local de NicOS Desktop — mismo patrón que trading_bot/jarvis-trabajo/jarvis-server.py
(http.server puro, sin frameworks, rutas manuales).

Corre DOS servidores HTTP en el mismo proceso:
  - servidor LOCAL, bind 127.0.0.1, puerto elegido por el SO (igual que v0.1) — lo usa
    la propia app Electron del Director (Nicolás), en la misma máquina. Confiado por
    definición (solo un proceso local puede llegar a 127.0.0.1) — sin verificación de token.
  - servidor de RED (v0.2.1: Tailscale, ya NO 0.0.0.0), puerto FIJO (NICOS_LAN_PORT) —
    lo usa la app Electron de la vista Operativa (Marianela) desde la PC Windows, a
    través de la red privada cifrada de Tailscale (nunca la LAN doméstica en claro).
    Bindea específicamente a NICOS_TAILSCALE_IP (obtenida con `tailscale ip -4` en
    esta Mac). Si esa variable no está seteada o `tailscale status` no confirma que
    el daemon esté corriendo, el servidor de red queda DESHABILITADO por completo
    (nunca cae a 0.0.0.0 como fallback) — se loguea claro por qué. Todas las rutas
    de tareas requieren `Authorization: Bearer <token>` emitido por el pairing (ver
    pairing.py), con rate limiting de intentos; nunca acepta credenciales de
    IA/Google — esas viven solo en la Mac.

Rutas nuevas de v0.2 bajo /api/v1/* (flujo de tareas con aprobación). Las rutas de v0.1
(/ping, /whatsapp/*, /director/*) siguen igual, solo servidas por el servidor LOCAL —
el chat y el editor directo del Sheet no se exponen a la LAN por diseño (siguen siendo
funciones del Director en su propia máquina).
"""
import json
import os
import subprocess
import sys
import threading
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

import ai_router
import centro_mando_adapter
import clinical_guard
import db
import pairing
import sheets_client
import tasks
import worker

LAN_PORT = int(os.getenv("NICOS_LAN_PORT", "47500"))
TAILSCALE_IP = os.getenv("NICOS_TAILSCALE_IP", "").strip()

# Rollback real (v0.2.1): apaga el flujo de tareas/aprobación sin tocar código
# ni perder lo demás -- /director/summary y /director/chat (lo que ya andaba
# en v0.1) siguen funcionando igual. No revierte movimientos ya ejecutados ni
# datos ya escritos en nicos.db -- eso, si hace falta, es manual (ver README).
TASK_FLOW_ENABLED = os.getenv("NICOS_TASK_FLOW_ENABLED", "true").strip().lower() != "false"


def _tailscale_running() -> bool:
    """Confirma que el daemon de Tailscale está activo, no solo que la variable
    de entorno esté seteada — evita bindear a una IP que ya no es válida (ej. el
    usuario desinstaló Tailscale pero la variable quedó vieja en el .env)."""
    try:
        result = subprocess.run(
            ["tailscale", "status", "--json"], capture_output=True, text=True, timeout=5
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False

JARVIS_TRABAJO_PATH = os.getenv(
    "NICOS_JARVIS_TRABAJO",
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

            if path.startswith("/api/v1/tasks") and not TASK_FLOW_ENABLED:
                self._send_json(503, {"ok": False, "error": "el flujo de tareas está desactivado (NICOS_TASK_FLOW_ENABLED=false)"})
                return

            if self._is_lan() and path in ("/whatsapp/messages", "/director/summary", "/api/v1/system/status"):
                self._send_json(403, {"ok": False, "error": "ruta no disponible en la red local"})
                return

            if path == "/whatsapp/messages":
                messages = sheets_client.list_messages()
                self._send_json(200, {"ok": True, "messages": messages})
            elif path == "/director/summary":
                self._send_json(200, {"ok": True, "summary": _director_summary()})
            elif path == "/api/v1/system/status":
                # v0.2.1-rc7 -- para "Acerca de NicOS". Deliberadamente NO
                # incluye la IP de Tailscale, el puerto, ni nada del
                # dispositivo -- solo booleanos/versiones, nada que identifique
                # la red ni permita ubicarla.
                self._send_json(200, {
                    "ok": True,
                    "core_running": True,
                    "tailscale_configured": bool(TAILSCALE_IP),
                    "tailscale_connected": _tailscale_running(),
                    "policy_version": centro_mando_adapter.POLICY_VERSION,
                    "policy_hash": centro_mando_adapter.POLICY_HASH,
                    "python_version": sys.version.split()[0],
                })
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
            elif path == "/api/v1/users":
                # Director-only, para "Personas con acceso a NicOS" -- combina
                # identidades ya vinculadas con códigos generados que todavía
                # nadie usó (ver pairing.list_users/list_pending_codes).
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                self._send_json(200, {
                    "ok": True,
                    "users": pairing.list_users(),
                    "pending": pairing.list_pending_codes(),
                })
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

            if path.startswith("/api/v1/tasks") and not TASK_FLOW_ENABLED:
                self._send_json(503, {"ok": False, "error": "el flujo de tareas está desactivado (NICOS_TASK_FLOW_ENABLED=false)"})
                return

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
                role = body.get("role", "operativa")
                turno = body.get("turno") or None
                display_name = body.get("display_name") or None
                try:
                    result = pairing.start_pairing(role, turno=turno, created_by="nicolas", display_name=display_name)
                    self._send_json(200, {"ok": True, **result})
                except pairing.PairingError as e:
                    self._send_json(400, {"ok": False, "error": str(e)})

            elif path == "/api/v1/pairing/complete":
                code = body.get("code", "")
                device_name = body.get("device_name", "Dispositivo sin nombre")
                try:
                    result = pairing.complete_pairing(
                        code, device_name,
                        display_name=body.get("display_name", ""),
                        dni=body.get("dni", ""),
                        fecha_nacimiento=body.get("fecha_nacimiento", ""),
                        sexo=body.get("sexo", ""),
                        pin=body.get("pin", ""),
                    )
                    self._send_json(200, {"ok": True, **result})
                except pairing.PairingError as e:
                    self._send_json(400, {"ok": False, "error": str(e)})

            elif path.startswith("/api/v1/pairing/") and path.endswith("/cancel"):
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                code = path.split("/")[4]
                pairing.cancel_pairing_code(code)
                self._send_json(200, {"ok": True})

            elif path == "/api/v1/pin/verify":
                # Selector local "¿Quién sos?" en un dispositivo ya autorizado --
                # requiere el token de dispositivo igual que cualquier ruta de
                # Operativa/Enfermero (no es una ruta nueva sin autenticación).
                auth = self._authenticate()
                if auth is None:
                    self._send_json(401, {"ok": False, "error": "token inválido o ausente"})
                    return
                pin = body.get("pin", "")
                ok = pairing.verify_pin_for_device(auth["device_id"], pin)
                if not ok:
                    self._send_json(401, {"ok": False, "error": "PIN incorrecto"})
                    return
                self._send_json(200, {"ok": True})

            elif path.startswith("/api/v1/users/") and path.endswith("/revoke"):
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                user_id = path.split("/")[4]
                pairing.revoke_user(user_id)
                self._send_json(200, {"ok": True})

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
                # Defensa en profundidad: el cliente (entrada-rapida.js) ya bloquea
                # esto antes de salir de la PC de Marianela — este chequeo repite la
                # misma heurística acá por si ese chequeo se bypaseó. No es la regla
                # real (esa es que el extractor de dominio nunca devuelve "clinico").
                clinico = clinical_guard.detect_clinical_data(raw_text)
                if clinico["blocked"]:
                    self._send_json(400, {"ok": False, "error": clinico["reason"]})
                    return
                # attachment_path YA NO se acepta del cliente (v0.2.1) — esa ruta
                # existiría en la PC de quien envía, no en la Mac. Sin upload real
                # implementado todavía, aceptar una ruta arbitraria no tiene sentido.
                result = tasks.create_task(
                    idempotency_key, auth["user_id"], auth["device_id"], raw_text,
                )
                # No se spawnea ningún thread acá — la tarea queda en 'received' y
                # el worker loop (worker.py, corriendo en su propio thread desde
                # main()) la recoge en su próximo ciclo (máx. 1s). Esto es lo que
                # hace que sobreviva a un reinicio del proceso a mitad de camino.
                self._send_json(200, {"ok": True, **result})

            elif path.startswith("/api/v1/tasks/") and path.endswith("/approve"):
                self._handle_task_action(path, body, "approve")
            elif path.startswith("/api/v1/tasks/") and path.endswith("/reject"):
                self._handle_task_action(path, body, "reject")
            elif path.startswith("/api/v1/tasks/") and path.endswith("/request-info"):
                self._handle_task_action(path, body, "request-info")

            elif path.startswith("/api/v1/tasks/") and path.endswith("/resolve-execution"):
                self._handle_resolve_execution(path, body)

            elif path.startswith("/api/v1/tasks/") and path.endswith("/provide-info"):
                self._handle_provide_info(path, body)

            elif path.startswith("/api/v1/devices/") and path.endswith("/revoke"):
                if self._is_lan():
                    self._send_json(403, {"ok": False, "error": "solo disponible localmente"})
                    return
                # index 4, no 3: "/api/v1/devices/<id>/revoke".split("/") == ['', 'api', 'v1', 'devices', '<id>', 'revoke']
                device_id = path.split("/")[4]
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
        # index 4, no 3: "/api/v1/tasks/<id>/approve".split("/") == ['', 'api', 'v1', 'tasks', '<id>', 'approve']
        # (bug preexistente, presente desde el tag v0.2-tareas-aprobacion -- corregido acá en v0.2.1-rc1)
        task_id = path.split("/")[4]
        try:
            if action == "approve":
                result = tasks.approve_task(
                    task_id, auth["user_id"], body.get("approved_action_hash", ""),
                    approved_task_revision=body.get("approved_task_revision"),
                )
                # tras aprobar queda en 'ready' — el worker loop la recoge sola,
                # no hace falta spawnear nada acá (ver worker.py).
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

    def _handle_resolve_execution(self, path, body):
        """Resolución exclusiva del Director (v0.2.1-rc1, punto 3) para una tarea
        needs_review causada por una interrupción a mitad de ejecución -- ver
        tasks.resolve_execution y worker.recover_orphaned_tasks. Mismo chequeo
        Director-only que aprobar/rechazar (nunca por red)."""
        if self._is_lan():
            self._send_json(403, {"ok": False, "error": "la resolución de ejecuciones se hace desde la Mac"})
            return
        auth = self._authenticate()
        task_id = path.split("/")[4]
        decision = body.get("decision", "")
        try:
            result = tasks.resolve_execution(task_id, auth["user_id"], decision, reason=body.get("reason"))
            self._send_json(200, {"ok": True, "task": result})
        except tasks.InvalidTransition as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._send_json(404, {"ok": False, "error": str(e)})

    def _handle_provide_info(self, path, body):
        """v0.2.1-rc6 -- el Director completa a mano lo que falta en una tarea
        'needs_information' (típicamente el dominio, cuando la IA lo dejó en
        "unknown"). CERO llamados a un proveedor de IA (ver
        worker.provide_missing_info) -- Director-only, igual que aprobar/
        rechazar/resolver, nunca por red."""
        if self._is_lan():
            self._send_json(403, {"ok": False, "error": "completar información se hace desde la Mac"})
            return
        auth = self._authenticate()
        task_id = path.split("/")[4]
        try:
            result = worker.provide_missing_info(task_id, auth["user_id"], body.get("updates", {}))
            self._send_json(200, {"ok": True, "task": result})
        except tasks.InvalidTransition as e:
            self._send_json(400, {"ok": False, "error": str(e)})
        except ValueError as e:
            self._send_json(404, {"ok": False, "error": str(e)})

def _serve(host, port, is_lan, ready_callback=None):
    server = ThreadingHTTPServer((host, port), Handler)
    server.is_lan = is_lan
    actual_port = server.server_address[1]
    if ready_callback:
        ready_callback(actual_port)
    server.serve_forever()


def main():
    db.run_migrations()
    db.integrity_check()

    local_port_holder = {}

    def _on_local_ready(port):
        local_port_holder["port"] = port
        # Primera línea de stdout: Electron la lee para saber a qué puerto local conectarse.
        print(f"NICOS_SIDECAR_PORT={port}", flush=True)
        sys.stderr.write(f"[sidecar] servidor LOCAL escuchando en http://127.0.0.1:{port}\n")

    # Worker loop durable — una tarea que quedó a mitad de camino en la corrida
    # anterior (crash, kill -9, apagón) se recupera acá, antes de aceptar tráfico
    # nuevo. Corre en su propio thread, separado de los servidores HTTP.
    worker_thread = threading.Thread(target=worker.run_forever, daemon=True)
    worker_thread.start()

    # Servidor de red: SOLO si hay una IP de Tailscale configurada Y el daemon está
    # activo. Nunca cae a 0.0.0.0 como fallback — si Tailscale no está listo, Marianela
    # simplemente no puede conectar todavía (con un mensaje claro), en vez de exponer
    # el sidecar a toda la red doméstica en texto plano.
    if not TAILSCALE_IP:
        sys.stderr.write(
            "[sidecar] NICOS_TAILSCALE_IP no está configurada — servidor de red DESHABILITADO. "
            "Solo el Director (esta Mac, vía localhost) puede usar la app. Para habilitar el "
            "acceso de Marianela: instalar Tailscale, correr `tailscale ip -4`, y configurar esa "
            "IP en Ajustes (o la variable de entorno NICOS_TAILSCALE_IP).\n"
        )
    elif not _tailscale_running():
        sys.stderr.write(
            f"[sidecar] NICOS_TAILSCALE_IP={TAILSCALE_IP} configurada pero `tailscale status` "
            "no responde — ¿Tailscale está instalado y corriendo en esta Mac? Servidor de red "
            "DESHABILITADO hasta que se resuelva.\n"
        )
    else:
        lan_thread = threading.Thread(
            target=_serve, args=(TAILSCALE_IP, LAN_PORT, True), daemon=True
        )
        lan_thread.start()
        sys.stderr.write(f"[sidecar] servidor de red (Tailscale) escuchando en {TAILSCALE_IP}:{LAN_PORT}\n")

    try:
        _serve("127.0.0.1", 0, False, ready_callback=_on_local_ready)
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
