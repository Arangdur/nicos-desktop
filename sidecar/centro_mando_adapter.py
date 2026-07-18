"""
Adapter formal alrededor de Centro de Mando — NO reescribe sus reglas ni su
script. `execute_action()` invoca por subprocess el MISMO
`registrar_movimiento.py cfo` que ya tiene 10 cargas reales verificadas
(17/07/2026); ese script sigue siendo el único punto de escritura a
`foto_financiera_*.md` y sigue escribiendo `REGISTRO_ENTRADA.md` solo —
no se duplica esa lógica acá.

Capas (extracción -> política -> ejecución), sin que la IA decida riesgo:
  classify_request()  -- determinístico, valida el dominio que extrajo la IA
  evaluate_risk()      -- determinístico, replica la regla SIMPLE / PENDIENTE DE TU OK
  prepare_action()     -- arma el comando exacto que se va a ejecutar (lo que se aprueba)
  execute_action()     -- corre registrar_movimiento.py (solo dominio cfo en esta iteración)
  validate_result()    -- interpreta el resultado del subprocess
  record_result()      -- transiciona la tarea a completed/failed/needs_review
"""
import hashlib
import os
import subprocess
import sys
import uuid

import yaml

import tasks

CENTRO_DE_MANDO_DIR = os.getenv(
    "CENTRO_DE_MANDO_DIR",
    "/Users/nicolasbuso/Claude/Projects/Centro de Mando",
)
REGISTRAR_MOVIMIENTO_PATH = os.path.join(CENTRO_DE_MANDO_DIR, "herramientas", "registrar_movimiento.py")

# La política de clasificación/riesgo vive en policies/risk_policy.yaml (raíz
# del proyecto NicOS Desktop), no acá — esta es la única fuente ejecutable,
# referenciada (no restateada) desde Centro de Mando/CLAUDE.md. Ver
# sidecar/tests/test_risk_policy_characterization.py antes de tocar el YAML.
#
# Dos formas de resolver el default, porque ninguna sola alcanza en los dos
# modos reales de ejecución:
# - Script normal (dev, o el test corrido desde cualquier cwd): __file__ es
#   confiable, así que se usa su ruta absoluta -- no importa desde dónde se
#   invoque `python3 .../test_risk_policy_characterization.py`.
# - Binario empaquetado por PyInstaller (`sys.frozen`): este módulo no vive en
#   un archivo real dentro de policies/.. -- ahí se confía en el cwd, que
#   sidecar-manager.js YA fija a ".../sidecar" (dev y empaquetado por igual),
#   con policies/ como hermano de sidecar/ (ver package.json build.*.extraResources).
if getattr(sys, "frozen", False):
    _DEFAULT_RISK_POLICY_PATH = os.path.join(os.getcwd(), "..", "policies", "risk_policy.yaml")
else:
    _DEFAULT_RISK_POLICY_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "policies", "risk_policy.yaml")
RISK_POLICY_PATH = os.getenv("NICOS_RISK_POLICY_PATH", _DEFAULT_RISK_POLICY_PATH)


_REQUIRED_POLICY_KEYS = ("version", "supported_domains", "logging_intents", "required_fields_cfo")


def _load_risk_policy(path: str) -> dict:
    """Falla rápido y claro si el YAML está mal armado -- mejor un error al
    arrancar el sidecar que un KeyError críptico la primera vez que alguien
    clasifica una tarea (punto 7 de la revisión post-v0.2.1)."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            raw = f.read()
    except OSError as e:
        raise ValueError(f"No se pudo leer la política de riesgo en '{path}': {e}") from e
    policy = yaml.safe_load(raw)
    if not isinstance(policy, dict):
        raise ValueError(f"'{path}' no es un YAML de mapeo válido (¿archivo vacío o mal formado?).")
    faltantes = [k for k in _REQUIRED_POLICY_KEYS if k not in policy]
    if faltantes:
        raise ValueError(
            f"'{path}' no tiene las claves requeridas: {faltantes}. "
            f"Esperadas: {list(_REQUIRED_POLICY_KEYS)}."
        )
    if not isinstance(policy["supported_domains"], list) or not policy["supported_domains"]:
        raise ValueError(f"'{path}': 'supported_domains' debe ser una lista no vacía.")
    if not isinstance(policy["logging_intents"], list):
        raise ValueError(f"'{path}': 'logging_intents' debe ser una lista.")
    if not isinstance(policy["required_fields_cfo"], list):
        raise ValueError(f"'{path}': 'required_fields_cfo' debe ser una lista.")
    return policy, raw


_POLICY, _POLICY_RAW_TEXT = _load_risk_policy(RISK_POLICY_PATH)

LOGGING_INTENTS = set(_POLICY["logging_intents"])
SUPPORTED_DOMAINS = set(_POLICY["supported_domains"])
REQUIRED_FIELDS_CFO = tuple(_POLICY["required_fields_cfo"])

# Trazabilidad de política por tarea (punto 6 de la revisión post-v0.2.1): cada
# tarea clasificada guarda qué versión/hash de esta política se usó en ese
# momento (ver worker._process_classification) -- si el YAML cambia después,
# queda registrado con cuál regla exacta se decidió cada caso pasado.
POLICY_VERSION = str(_POLICY["version"])
POLICY_HASH = hashlib.sha256(_POLICY_RAW_TEXT.encode("utf-8")).hexdigest()

INTENT_TO_TIPO = {
    "register_expense": "gasto",
    "register_income": "ingreso",
}


class UnsupportedDomain(Exception):
    pass


def classify_request(extracted: dict):
    """Devuelve el dominio ('cfo'|'abate') si es válido y soportado, o None si
    hace falta más información (dominio ambiguo/ausente) o está fuera de alcance
    (cualquier cosa que no sea cfo/abate — clínico incluido, por construcción
    nunca llega acá con un dominio soportado)."""
    domain = (extracted or {}).get("domain")
    if domain in SUPPORTED_DOMAINS:
        return domain
    return None


def evaluate_risk(domain: str, intent: str, extracted: dict) -> str:
    """'simple' (auto-ejecutar) o 'approval_required' (PENDIENTE DE TU OK).
    Default seguro: approval_required — solo el caso explícito de "registrar
    un movimiento ya ocurrido, con todos los datos" es simple."""
    if intent not in LOGGING_INTENTS:
        return "approval_required"
    missing = [f for f in REQUIRED_FIELDS_CFO if not extracted.get(f)]
    if missing:
        return "approval_required"
    return "simple"


def prepare_action(domain: str, intent: str, extracted: dict) -> dict:
    """Arma la acción EXACTA que se va a ejecutar — esto es lo que se hashea
    para la aprobación versionada (tasks.compute_action_hash)."""
    if domain == "cfo":
        tipo = INTENT_TO_TIPO.get(intent)
        if tipo is None:
            raise UnsupportedDomain(f"Intent '{intent}' no soportado para dominio cfo en esta iteración.")
        return {
            "domain": "cfo",
            "command": "registrar_movimiento.py",
            "args": {
                "fecha": extracted.get("date"),
                "concepto": extracted.get("concept"),
                "monto": extracted.get("amount"),
                "tipo": tipo,
                "detalle": extracted.get("evidence", ""),
            },
        }
    if domain == "abate":
        return {
            "domain": "abate",
            "command": "manual_via_centro_de_mando",
            "args": {
                "proveedor": extracted.get("concept"),
                "monto": extracted.get("amount"),
                "detalle": extracted.get("evidence", ""),
            },
            "nota": (
                "Abate no se automatiza en esta iteración — el puente de navegador "
                "al Google Sheet no es invocable desde este sidecar. Completar manualmente "
                "vía el flujo actual de Centro de Mando en Claude Code."
            ),
        }
    raise UnsupportedDomain(f"Dominio '{domain}' no soportado.")


def execute_action(task_id: str, prepared_action: dict) -> dict:
    """Ejecuta la acción. Para cfo: subprocess real sobre el script existente,
    sin cambios en su lógica (solo se le agregó --operation-id, aditivo). Para
    abate: nunca se auto-ejecuta — devuelve needs_review.

    Idempotencia del efecto externo, con un ledger de ejecución de 5 estados
    (ver migración 003 y reconcile_execution_attempt más abajo):
    'claimed' (fila creada, subprocess todavía no invocado) -> 'effect_started'
    (subprocess ya invocado) -> 'effect_confirmed'/'effect_failed' (resultado
    conocido). Si el proceso muere mientras está 'claimed', hay certeza total
    de que no hubo efecto externo. Si muere en 'effect_started', la recuperación
    al reiniciar (worker.recover_orphaned_tasks) reconcilia contra el ledger
    durable de registrar_movimiento.py en vez de asumir 'uncertain' a ciegas."""
    domain = prepared_action.get("domain")

    if domain == "abate":
        return {
            "ok": False,
            "needs_manual_review": True,
            "message": prepared_action.get("nota", "Requiere completarse manualmente."),
        }

    if domain != "cfo":
        raise UnsupportedDomain(f"execute_action no soporta dominio '{domain}'.")

    args = prepared_action["args"]
    operation_id = str(uuid.uuid4())
    attempt = tasks.create_execution_attempt(task_id, operation_id, executor="centro_mando_adapter")

    cmd = [
        "python3", REGISTRAR_MOVIMIENTO_PATH, "cfo",
        "--fecha", str(args["fecha"]),
        "--concepto", str(args["concepto"]),
        "--monto", str(args["monto"]),
        "--tipo", str(args["tipo"]),
        "--operation-id", operation_id,
    ]
    if args.get("detalle"):
        cmd += ["--detalle", str(args["detalle"])]

    # A partir de acá, si el proceso muere, la recuperación tiene que reconciliar
    # contra el ledger en vez de asumir nada -- por eso el cambio explícito de
    # estado justo antes de la única línea que realmente puede tener el efecto externo.
    tasks.update_execution_attempt_status(attempt["execution_id"], "effect_started")

    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30, shell=False)
        result = {
            "ok": proc.returncode == 0,
            "returncode": proc.returncode,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "operation_id": operation_id,
        }
    except subprocess.TimeoutExpired as e:
        result = {"ok": False, "returncode": None, "stdout": "", "stderr": f"timeout: {e}", "operation_id": operation_id}

    status = "effect_confirmed" if validate_result(result) else "effect_failed"
    tasks.finish_execution_attempt(
        attempt["execution_id"], status,
        result_json=result, error_message=None if status == "effect_confirmed" else result.get("stderr"),
    )
    return result


def _read_operation_ledger():
    """Lee el ledger durable de operation_id ya procesados por
    registrar_movimiento.py (mismo archivo que el script usa para deduplicar,
    ver _operation_already_processed ahí). Devuelve un set() de operation_id,
    o None si el archivo existe pero no se pudo leer (I/O real -- 'no existe
    todavía' es un set vacío legítimo, no una falla)."""
    ledger_path = os.getenv(
        "NICOS_OPERATION_LEDGER", os.path.join(CENTRO_DE_MANDO_DIR, "OPERATION_IDS_PROCESADOS.txt")
    )
    try:
        if not os.path.exists(ledger_path):
            return set()
        with open(ledger_path, "r", encoding="utf-8") as f:
            return {line.strip() for line in f if line.strip()}
    except OSError:
        return None


def reconcile_execution_attempt(attempt: dict) -> str:
    """Determina el desenlace REAL de un intento que quedó a mitad tras un
    reinicio, consultando el ledger durable en vez de adivinar (punto 1 de la
    revisión post-v0.2.1: 'el operation_id debe quedar asociado a un recibo
    verificable, no solo al ledger SQLite'). Devuelve 'effect_confirmed',
    'effect_failed' o 'uncertain'. Nunca reintenta nada -- solo diagnostica."""
    if attempt["status"] == "claimed":
        # El subprocess nunca se invocó -- ni siquiera pudo haber reclamado el
        # operation_id en el ledger de registrar_movimiento.py. Certeza total.
        return "effect_failed"
    ledger = _read_operation_ledger()
    if ledger is None:
        return "uncertain"
    if attempt["operation_id"] in ledger:
        return "effect_confirmed"
    # Ausencia en el ledger = certeza de que nunca se escribió: el orden
    # "reclamar antes de escribir" en registrar_movimiento.py garantiza que un
    # operation_id que no está en el ledger nunca llegó a tocar foto_financiera_*.md.
    return "effect_failed"


def validate_result(execution_result: dict) -> bool:
    if execution_result.get("needs_manual_review"):
        return False
    return bool(execution_result.get("ok")) and execution_result.get("stdout", "").startswith("OK:")


def record_result(task_id: str, actor: str, execution_result: dict):
    """Transiciona la tarea según el resultado. `registrar_movimiento.py` ya
    escribió REGISTRO_ENTRADA.md por su cuenta — acá solo se refleja el
    resultado en la máquina de estados / auditoría estructurada (task_events)."""
    if execution_result.get("needs_manual_review"):
        return tasks.transition(
            task_id, "needs_review", actor,
            detail={"motivo": execution_result.get("message")},
            result_json=execution_result,
        )
    if validate_result(execution_result):
        return tasks.transition(
            task_id, "completed", "system",
            detail={"resultado": execution_result.get("stdout")},
            result_json=execution_result,
        )
    return tasks.transition(
        task_id, "failed", "system",
        detail={"error": execution_result.get("stderr") or execution_result.get("stdout")},
        result_json=execution_result,
        error_message=execution_result.get("stderr") or execution_result.get("stdout"),
    )
