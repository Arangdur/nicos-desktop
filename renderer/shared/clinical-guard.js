// Defensa adicional (v0.2.1), NO la regla real: la regla real es que el
// extractor de dominio (ai_router.py) solo puede devolver "cfo"/"abate"/
// "unknown" -- clínico nunca es un dominio soportado (ver
// centro_mando_adapter.SUPPORTED_DOMAINS y policies/risk_policy.yaml), así
// que estructuralmente ningún texto clínico puede terminar ejecutando nada.
//
// Esto es una segunda capa, heurística y deliberadamente sobre-inclusiva: si
// el texto que Marianela va a mandar tiene pinta de contener datos
// identificatorios de un paciente, se bloquea ANTES de salir de esta PC — ni
// siquiera llega a la cola/outbox. El servidor (sidecar/clinical_guard.py)
// repite la misma heurística por si este chequeo del cliente se bypasea.
function detectClinicalData(text) {
  if (!text) return { blocked: false };

  if (/\bdni\b/i.test(text) && /\b\d{1,3}[.,]?\d{3}[.,]?\d{3}\b/.test(text)) {
    return { blocked: true, reason: 'El texto parece incluir un DNI — no se puede enviar por acá.' };
  }

  if (/diagn[oó]stico|receta\s+de|historia\s+cl[ií]nica/i.test(text)) {
    return { blocked: true, reason: 'El texto parece incluir información clínica (diagnóstico/receta/historia clínica) — no se puede enviar por acá.' };
  }

  // Nombre + Apellido (dos palabras con mayúscula inicial) cerca de "paciente".
  if (/paciente/i.test(text) && /\b[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\s+[A-ZÁÉÍÓÚÑ][a-záéíóúñ]+\b/.test(text)) {
    return { blocked: true, reason: 'El texto parece nombrar a un paciente — no se puede enviar por acá.' };
  }

  return { blocked: false };
}
