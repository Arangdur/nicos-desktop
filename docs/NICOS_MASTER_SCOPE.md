# NicOS — Documento maestro de alcance

Fecha: 19/7/2026. Reemplaza la idea de que `v0.2.1` era "casi el producto final" — no lo era. Esto es el reinicio de producto (no de código) acordado tras revisar la devolución cruzada de ChatGPT y auditar en profundidad Centro de Mando, Agente Médico Integral, CFO y Decisiones Estratégicas, Director operativo Abate y Jefe de Gabinete Personal.

## 1. Qué es NicOS ahora

Un solo sistema que centraliza todos los frentes de Nicolás — clínico, administrativo, institucional (Abate), financiero, personal y trading — con un modelo de roles extensible (no solo Director/Operativa) y permisos reales por rol, no un sistema separado por frente.

**No reemplaza** a las herramientas de sistema de registro que ya existen y funcionan (DRAPP como historia clínica, MisRx/PAMI/Traditum como recetarios, Google Sheet de Abate) — las orquesta, prepara datos para ellas, y centraliza lo que hoy está repartido en 4 Proyectos de Claude.ai + Jarvis + un bot n8n bloqueado.

## 2. Decisiones ya tomadas (no reabrir sin motivo nuevo)

- **Todo dentro de un solo ecosistema**: NicOS Desktop absorbe lo que hoy hacen Centro de Mando, Agente Médico Integral (módulo clínico), y el bot de WhatsApp — no se mantienen como sistemas separados.
- **El bot "NicOS" de n8n se retira.** Su función (WhatsApp de pacientes: turnos, recetas) pasa a NicOS Desktop. Un solo sistema con ese nombre, no dos.
- **La IA clínica corre dentro de NicOS, vía API paga** (no vía la suscripción de Claude.ai) — el costo estimado (~USD 20-70/mes en el uso actual) se justifica frente al valor. Requisito de diseño: configurar un tope de gasto mensual en las cuentas de Anthropic y OpenAI antes de habilitar el módulo clínico, y medir el gasto real del primer mes.
- **Marianela nunca ve contenido clínico** — ni evoluciones, ni diagnósticos, ni notas de sesión, ni formulaciones. Ve exclusivamente lo administrativo (turnos, contacto, obra social, estado de recetas/certificados ya preparados).
- **DRAPP sigue siendo la única historia clínica real.** NicOS prepara texto listo para pegar ahí (como ya hace el prompt Plaud→DRAPP), no la reemplaza ni hace scraping retroactivo de su historial.
- **El trading bot nunca da señales de compra/venta autónomas** — regla ya vigente, no cambia.
- **Ningún dato clínico se procesa por automatización sin supervisión tuya en el momento** — la diferencia real no es "IA sí/no", es "vos interactuando y revisando antes de que algo se guarde" vs. un proceso de fondo sin nadie mirando.
- **El sistema de roles es extensible, no fijo a dos.** Hoy (rc10) el rol se elige una sola vez, al primer arranque, y queda fijo para esa instalación. Eso deja de alcanzar en cuanto hay más de dos tipos de usuario — pasa a ser vos (Director) quien asigna el rol al aceptar cada vinculación nueva, no cada dispositivo el que se autoasigna uno al abrir la app por primera vez.

## 3. Modelo de usuarios extensible

Se agrega un tercer rol (y el sistema queda preparado para más en el futuro, sin reescribir código cada vez):

### Rol nuevo: Enfermero/a de Abate

- **Qué reportan**: las tres categorías que confirmaste — operativo (medicación administrada, comidas, actividades, incidentes), clínico-conductual (ánimo, síntomas, episodios de crisis) y administrativo (asistencia, visitas de familiares, mantenimiento).
- **Sin procesamiento de IA — CONFIRMADO.** La novedad se guarda exactamente como la escribe el enfermero, sin resumen, sin redacción automática, sin ningún llamado a Claude/OpenAI. Esto saca a este módulo por completo del problema de fondo de la sección legal (§8) — ningún dato de un residente sale hacia un proveedor externo de IA por esta vía. La consulta legal pendiente sigue aplicando al Módulo Clínico de consultorio (Plaud→borrador con IA), no a las novedades de enfermería.
- **Quién ve esos reportes**: vos (Director) y Albano/Daniela (socios de Abate).

  **Confirmación pendiente, léela con atención**: esto le da a Albano y Daniela acceso a observaciones clínico-conductuales de los residentes (ánimo, síntomas, crisis) — no solo lo operativo/administrativo. Es un nivel de exposición distinto al que le diste a Marianela sobre pacientes de consultorio (cero contenido clínico, sin excepción). Para Abate decidiste que sí corresponde, dado que Albano/Daniela ya son socios institucionales con responsabilidad sobre los residentes — lo dejo así, pero quería que quede escrito en negro sobre blanco antes de construirlo, no asumido en silencio.
- **Continuidad de turno entre enfermeros — CONFIRMADO, sí existe.** El enfermero que entra ve lo que reportaron los turnos anteriores (no solo lo suyo) — necesario para la continuidad de cuidado en una Casa de Medio Camino.
- **Vinculación — CONFIRMADO, individual.** Un dispositivo/login por enfermero, mismo mecanismo que Marianela (código de 6 dígitos generado por vos, por Tailscale, revocable de forma individual) — el código lleva asociado qué rol le corresponde a ese dispositivo, decidido por vos al generarlo, no elegido por quien lo recibe. Esto también da trazabilidad real: cada novedad queda asociada a qué enfermero la reportó, no a "la casa" en general.

### Implicancia técnica de fondo

Hoy `main.js` tiene el rol Director/Operativa hardcodeado en dos ramas de código, elegido una sola vez por quien abre la app. Pasa a ser: el rol vive por dispositivo vinculado (ya existe la tabla `devices`, se le agrega qué rol tiene cada uno), lo asigna el Director al momento de aceptar el pairing, y los permisos de cada pantalla se resuelven contra ese rol en vez de estar fijos en el código de cada vista. Esto es lo que permite agregar un cuarto o quinto rol en el futuro (por ejemplo, si algún día Albano necesita su propio acceso directo) sin rehacer la arquitectura de nuevo.

## 4. Arquitectura de módulos

```
NicOS Desktop
│
├── Módulo Clínico                          [Director únicamente]
│   ├── Pacientes (búsqueda, ficha básica)
│   ├── Evoluciones (Plaud → borrador → vos revisás → guardás/copiás a DRAPP)
│   ├── Psiquiatría (DSM-5/CIE-11, estado mental)
│   ├── Informes y certificados (derivación, peritaje, alta)
│   ├── Resumen de evidencia clínica (IA + búsqueda)
│   └── Recetas — FASE POSTERIOR, ver §7 (riesgo alto: credenciales reales de prescripción)
│
├── Consultorio administrativo               [Director + Operativa, vista administrativa]
│   ├── Turnos (hoy vía DRAPP, NicOS puede reflejar/avisar, no reemplaza el calendario)
│   ├── WhatsApp pacientes (reemplaza al bot n8n — Twilio)
│   ├── Recetas pendientes (estado, no contenido clínico)
│   ├── Obras sociales / cobertura
│   └── Cobros / Link de pago
│
├── Fundación Abate                          [Director; Operativa ve autorizaciones si aplica]
│   ├── Autorizaciones (ya existe en rc10)
│   ├── Movimientos (Google Sheet compartido con Albano/Daniela — ya existe)
│   ├── Novedades de enfermería (NUEVO — rol Enfermero reporta, ven Director + Albano + Daniela; texto tal cual, sin IA)
│   ├── Informes y actas
│   ├── Equipo / residentes
│   └── Indicadores
│
├── Finanzas                                 [Director únicamente]
│   ├── Personal (foto_financiera_*.md — ya existe en rc10)
│   ├── Abate (ya existe en rc10)
│   └── Patrimonio (lectura de CFO_Nico_*.json — es un export externo, solo lectura, NicOS nunca lo genera)
│
├── Jefe de Gabinete                         [Director únicamente]
│   ├── Briefing diario
│   ├── Check-in de bienestar (ejercicio/sueño/alimentación)
│   ├── Tareas y prioridades
│   └── Biblioteca de prompts (ya existe como documento, migrar a NicOS)
│
├── Trading                                  [Director únicamente, ampliación del Resumen actual]
│   ├── Panel ampliado (hoy es solo snapshot de lectura)
│   ├── Backtesting / paper trading (visibilidad, no ejecución vía NicOS)
│   └── Sin señales operativas autónomas — regla dura, no cambia
│
└── Centro de Mando (motor transversal, ya es el corazón de rc10)
    ├── Routing CFO/Abate (ya existe: centro_mando_adapter.py)
    ├── Riesgo y aprobaciones (ya existe: risk_policy.yaml, máquina de estados)
    ├── IA Claude/OpenAI (ya existe: ai_router.py, matriz de proveedores)
    └── Auditoría (ya existe: task_events, idempotencia, recuperación de caídas)
```

## 5. Lo que ya está construido y se reutiliza tal cual (rc10)

No se tira nada de lo hecho hasta ahora — es la base técnica de todo lo de arriba:

- Roles Director/Operativa con candado real (no se puede escalar de Operativa a Director desde la UI) — se extiende a N roles según §3, sin perder esta garantía.
- Pairing por código de 6 dígitos + token revocable, red exclusivamente por Tailscale.
- Máquina de estados de tareas (`received → ... → completed/failed/cancelled/needs_review`), con aprobación versionada (`task_revision` + hash de acción) y auditoría append-only (`task_events`).
- Idempotencia real (`idempotency_key` único) y recuperación de tareas huérfanas tras un crash o reinicio.
- `ai_router.py` con matriz de proveedores (Claude/OpenAI, con fallback) — reutilizable para el módulo clínico, no hay que reescribirlo.
- Empaquetado separado por plataforma (Mac lleva el sidecar completo, Windows NO — hallazgo y fix de esta misma sesión, rc10).
- Diagnósticos sanitizados (`exportar_logs_mac.sh` / `.ps1`) y pantalla "Acerca de" con metadata de build verificable.
- `centro_mando_adapter.py` como capa de negocio CFO/Abate — el mismo patrón (clasificar → preparar acción → hash → aprobar/ejecutar) es el que va a usar el módulo clínico para "preparar nota → vos revisás → guardar".

## 6. Modelo de permisos por rol (explícito, no implícito)

| Dato / acción | Director (Nicolás) | Operativa (Marianela) | Enfermero/a (Abate) |
|---|---|---|---|
| Evoluciones, diagnósticos, notas de sesión (consultorio) | Lectura y escritura | **Sin acceso, ni siquiera agregado** | Sin acceso |
| Turnos (fecha/hora, nombre, obra social, teléfono) | Lectura y escritura | Lectura y escritura | Sin acceso |
| Recetas — estado ("pendiente"/"lista para retirar") | Lectura y escritura | Lectura y escritura | Sin acceso |
| Recetas — contenido (droga, dosis, diagnóstico CIE-10) | Lectura y escritura | **Sin acceso** | Sin acceso |
| WhatsApp de pacientes | Lectura y escritura | Lectura y escritura (es su frente principal) | Sin acceso |
| CFO (finanzas personales) | Lectura y escritura | Sin acceso | Sin acceso |
| Abate — autorizaciones y movimientos | Lectura y escritura | Sin acceso (salvo que decidas lo contrario más adelante) | Sin acceso |
| Abate — novedades de enfermería (operativo + clínico-conductual + administrativo) | Lectura y escritura | Sin acceso | **Lectura y escritura** — ve también lo reportado por otros turnos (continuidad de turno confirmada), cada reporte queda trazado a qué enfermero lo escribió |
| Trading | Lectura | Sin acceso | Sin acceso |
| Jefe de Gabinete | Lectura y escritura | Sin acceso | Sin acceso |
| Claves de API, credenciales | Solo Director, nunca salen de la Mac | Sin acceso (ya así en rc10) | Sin acceso |

Nota: Albano y Daniela no son roles de NicOS (no tienen dispositivo propio vinculado hoy) — siguen viendo las novedades de enfermería por el mismo canal que ya usan para Abate (hoy Google Sheet; si migran a NicOS en el futuro, ahí sí serían un rol más de esta tabla).

## 7. Fuera del primer corte, a propósito

**Automatizar la emisión de recetas** (MisRx, PAMI Receta Electrónica, Traditum) queda deliberadamente afuera de la primera versión del módulo clínico. Son sistemas donde se prescribe con tu matrícula real (MN 132682 / MP 39083) — el riesgo de un error de automatización ahí es de otra categoría que clasificar un gasto. Primer corte: NicOS prepara los datos (paciente, droga, dosis, diagnóstico) listos para que vos los tipees en el portal correspondiente — no opera el portal por vos. Automatizar la emisión en sí es una decisión aparte, para después de que el módulo clínico básico esté probado en uso real.

## 8. Pendiente de tu decisión (no lo resuelvo yo solo)

- **Revisión legal del Módulo Clínico de consultorio** (Plaud→borrador con IA, informes, resúmenes de evidencia): Ley 26.657 (Salud Mental), Ley 26.529 (Historia Clínica) y Ley 25.326 (Protección de Datos) — no soy abogado, esto necesita que lo confirmes con uno antes de que un dato real de un paciente pase por la API de un proveedor de IA. **Ya no alcanza a las novedades de enfermería de Abate**, que quedaron sin IA (ver §3) — ese módulo puede avanzar sin esperar esta revisión.

  Checklist para esa consulta, para que sea concreta y rápida:
  1. A quién consultar: abogado/estudio especializado en protección de datos personales + derecho de la salud en Argentina — si Abate ya tiene uno institucional, empezar por ahí.
  2. Qué llevar: términos de servicio/DPA de la API de Anthropic y OpenAI (`console.anthropic.com` / `platform.openai.com`); el texto exacto de lo que se manda (transcripción de Plaud → borrador, ver mockup `mockups/modulo-clinico.html`); el consentimiento informado que ya usás con pacientes; la tabla de permisos de §6.
  3. Pregunta puntual a resolver: si el consentimiento actual cubre "tus datos pueden ser procesados por un proveedor externo de IA para asistir en la redacción", o si hace falta agregar una cláusula específica.
  4. Mientras tanto: el módulo clínico sigue en mockup con datos inventados — no se carga un dato real de un paciente hasta que esto esté resuelto.
- **Unificar la regla de privacidad clínica**, hoy duplicada en 3 lugares (`jarvis-trabajo/CLAUDE.md`, tarea `centro-de-mando-semanal`, comentario en `dashboard.html`) — decidir una única fuente.
- **Qué pasa con Centro de Mando y Jarvis** una vez que NicOS Desktop cubre más terreno — ¿se retiran, quedan como capa de conversación libre sobre los mismos datos, o se mantienen para lo que NicOS no cubra (los otros 3 Proyectos Cowork que no se integren)?

## 9. Orden de implementación propuesto

1. **Congelar `rc10`** como base técnica (ya está — es este mismo commit).
2. Migrar el modelo de roles de "fijo por instalación" a "asignado por dispositivo" (§3) — es la base para todo lo que sigue, incluido el rol Enfermero.
3. Diseñar el modelo de datos y permisos del módulo clínico (nuevas migraciones, cifrado adicional para lo clínico, separado de lo financiero).
4. Migrar el WhatsApp de pacientes de n8n a NicOS Desktop (Twilio, ya migrado de Meta — reutilizar esa configuración).
5. Construir el módulo clínico mínimo: buscar paciente, ver evoluciones agregadas propias, Plaud→borrador de nota vía IA, revisar, guardar, copiar a DRAPP.
6. Ajustar la vista Operativa al modelo de permisos de la tabla del §6.
7. Construir el rol Enfermero/a de Abate (novedades operativo + clínico-conductual + administrativo), una vez resueltas las preguntas del §8.
8. Integrar lectura de patrimonio (`CFO_Nico_*.json`) al módulo de Finanzas, si seguís queriéndolo.
9. Ampliar el panel de Trading (sin tocar la regla de "sin señales autónomas").
10. Migrar Jefe de Gabinete (briefing, bienestar, biblioteca de prompts) a NicOS.
11. **Recién ahí**: retomar el `.exe` de Windows, la prueba física con Marianela (y ahora también con al menos un enfermero de Abate), y la llamada real a OpenAI — con el alcance correcto, para no instalarle a nadie algo que va a cambiar de nuevo en dos semanas.

## 10. Lo que NO cambia respecto a lo ya acordado esta sesión

- Nunca se ejecuta una acción financiera o clínica real sin tu aprobación explícita cuando el riesgo lo requiere (regla SIMPLE vs. PENDIENTE DE TU OK, ya vigente).
- Nunca se inventan datos — si falta información, se pregunta o se marca "pendiente", nunca se completa solo.
- Todo cambio de código sigue recibiendo su propio release candidate, con test suite en verde antes de tagear.
