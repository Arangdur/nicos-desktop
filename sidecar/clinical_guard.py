"""
Defensa adicional (v0.2.1), NO la regla real: la regla real es que el
extractor de dominio (ai_router.py) solo puede devolver "cfo"/"abate"/
"unknown" -- clínico nunca es un dominio soportado (ver
centro_mando_adapter.SUPPORTED_DOMAINS y policies/risk_policy.yaml), así que
estructuralmente ningún texto clínico puede terminar ejecutando nada.

Esta es la contraparte de renderer/shared/clinical-guard.js: repite la misma
heurística del lado del servidor por si el chequeo del cliente se bypasea
(DevTools, un cliente distinto, un bug). Deliberadamente sobre-inclusiva.
"""
import re

_DNI_CONTEXT = re.compile(r"\bdni\b", re.IGNORECASE)
_DNI_NUMBER = re.compile(r"\b\d{1,3}[.,]?\d{3}[.,]?\d{3}\b")
_CLINICAL_KEYWORDS = re.compile(r"diagn[oó]stico|receta\s+de|historia\s+cl[ií]nica", re.IGNORECASE)
_PACIENTE_KEYWORD = re.compile(r"paciente", re.IGNORECASE)
_PROPER_NAME_PAIR = re.compile(r"\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b")


def detect_clinical_data(text: str) -> dict:
    if not text:
        return {"blocked": False}

    if _DNI_CONTEXT.search(text) and _DNI_NUMBER.search(text):
        return {"blocked": True, "reason": "El texto parece incluir un DNI."}

    if _CLINICAL_KEYWORDS.search(text):
        return {"blocked": True, "reason": "El texto parece incluir información clínica (diagnóstico/receta/historia clínica)."}

    if _PACIENTE_KEYWORD.search(text) and _PROPER_NAME_PAIR.search(text):
        return {"blocked": True, "reason": "El texto parece nombrar a un paciente."}

    return {"blocked": False}
