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
import os
import subprocess

import tasks

CENTRO_DE_MANDO_DIR = os.getenv(
    "CENTRO_DE_MANDO_DIR",
    "/Users/nicolasbuso/Claude/Projects/Centro de Mando",
)
REGISTRAR_MOVIMIENTO_PATH = os.path.join(CENTRO_DE_MANDO_DIR, "herramientas", "registrar_movimiento.py")

# Intents que son "transcribir algo que ya pasó" — la única excepción documentada
# en Centro de Mando/CLAUDE.md para no pedir OK ("es transcribir lo que Nicolás
# mismo te contó, no una decisión tuya"). Cualquier otro intent (pagar, transferir,
# lo que sea que implique decidir algo nuevo) requiere aprobación por defecto.
LOGGING_INTENTS = {"register_expense", "register_income"}

SUPPORTED_DOMAINS = {"cfo", "abate"}

INTENT_TO_TIPO = {
    "register_expense": "gasto",
    "register_income": "ingreso",
}

REQUIRED_FIELDS_CFO = ("amount", "date", "concept")


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


def execute_action(prepared_action: dict) -> dict:
    """Ejecuta la acción. Para cfo: subprocess real sobre el script existente,
    sin cambios. Para abate: nunca se auto-ejecuta — devuelve needs_review."""
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
    cmd = [
        "python3", REGISTRAR_MOVIMIENTO_PATH, "cfo",
        "--fecha", str(args["fecha"]),
        "--concepto", str(args["concepto"]),
        "--monto", str(args["monto"]),
        "--tipo", str(args["tipo"]),
    ]
    if args.get("detalle"):
        cmd += ["--detalle", str(args["detalle"])]

    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    return {
        "ok": proc.returncode == 0,
        "returncode": proc.returncode,
        "stdout": proc.stdout.strip(),
        "stderr": proc.stderr.strip(),
    }


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
