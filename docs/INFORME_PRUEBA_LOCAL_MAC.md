# Informe de prueba local — NicOS Desktop, dos instancias en la misma Mac

Prueba previa a la sesión física con Marianela, hecha enteramente en la Mac de Nicolás: dos instancias de NicOS Desktop corriendo en paralelo (Director + Operativa simulada), sobre el commit `v0.2.1-rc4`. Objetivo: agotar todo lo que se puede probar sin una segunda máquina antes de la prueba física real.

## Metadata

| Campo | Valor |
|---|---|
| Commit base | `6cb836a43e8c6ffbd1c8ed64d2d3ced0b9a01fa2` (tag `v0.2.1-rc4`) |
| Rama | `feature/nicos-v0.2` |
| OS | macOS 15.6.1, Darwin 24.6.0, arm64 |
| Fecha | 18/7/2026 |
| Cambios de código durante esta prueba | Ninguno en la lógica de rc4 -- solo se agregaron `scripts/run_test_director_mac.sh` y `scripts/run_test_operativa_mac.sh` (tooling de ejecución, no lógica de la app) |

## Hallazgo importante: el aislamiento de `--user-data-dir` de Electron NO alcanza

Al arrancar dos instancias con `--user-data-dir` distintos (aislando settings/tokens/outbox de Electron, tal como se pidió), la instancia Director abrió su Ajustes y mostró un dispositivo pareado de una sesión de pruebas anterior — la base `nicos.db` real del proyecto, no una vacía.

**Causa**: `sidecar/db.py` resuelve `nicos.db` por una ruta relativa al propio código del sidecar (`os.path.dirname(__file__)`), sin ninguna relación con qué instancia de Electron lo lanzó. El aislamiento de Electron (`--user-data-dir`) y el aislamiento de datos del sidecar (`nicos.db`, `foto_financiera_*.md`, `REGISTRO_ENTRADA.md`, ledger de `operation_id`) son dos mecanismos completamente independientes -- aislar uno no aísla el otro.

**Corrección**: se relanzó la instancia Director con `NICOS_DB_PATH`, `NICOS_CFO_DIR`, `NICOS_REGISTRO_ENTRADA`, `NICOS_OPERATION_LEDGER` y `NICOS_JARVIS_TRABAJO` apuntando a una carpeta de prueba completamente separada (variables de entorno que el sidecar ya soportaba, sin ningún cambio de código). Confirmado con el mtime del archivo (`stat -f "%Sm"`) que la `nicos.db` real del proyecto no se tocó en ningún momento.

**Prevención permanente**: se formalizó esto en `scripts/run_test_director_mac.sh`, con guardas que abortan si alguna de esas rutas queda sin definir o resuelve dentro de una ubicación productiva conocida (ver sección siguiente).

## Scripts nuevos: `run_test_director_mac.sh` / `run_test_operativa_mac.sh`

Ambos en `scripts/`. El de Director fuerza las 5 rutas de datos del sidecar a una carpeta de prueba (`/tmp/nicos-local-test` por defecto, configurable con `NICOS_TEST_ROOT`) y **verifica, no asume**, que ninguna resultó apuntando a producción antes de arrancar Electron.

Probado en vivo, los 5 casos:

| Caso | Resultado |
|---|---|
| `NICOS_TEST_ROOT` dentro de `CFO y Decisiones Estrategicas` | ❌ Abortó antes de arrancar Electron |
| `NICOS_TEST_ROOT` dentro de `Centro de Mando` | ❌ Abortó antes de arrancar Electron |
| `NICOS_TEST_ROOT` dentro de `jarvis-trabajo` | ❌ Abortó antes de arrancar Electron |
| `NICOS_DB_PATH` apuntando al `nicos.db` real del propio repo | ❌ Abortó (patrón detectado explícitamente) |
| `NICOS_TEST_ROOT` en una ruta segura de `/tmp` | ✅ Pasó todas las verificaciones, arrancó Electron |

El script de Operativa no necesita estas guardas -- esa vista nunca arranca sidecar propio ni toca `nicos.db`/`foto_financiera_*.md` directamente (todo pasa por HTTP contra el sidecar del Director), confirmado leyendo `electron/main.js`.

## Resultados de la prueba (sin red, ambas instancias en la misma Mac)

### Aislamiento completo confirmado
- Dos procesos Electron simultáneos, `nicos-settings.json` separado en cada `userData`, roles correctos (`director` / `operativa`) persistidos independientemente.
- `nicos.db`, `foto_financiera_testmes.md`, `REGISTRO_ENTRADA.md` y el ledger de `operation_id` de la sesión de prueba, todos en `/tmp/nicos-local-test/`, nunca en las rutas reales del proyecto (confirmado por mtime sin cambios en los archivos reales).

### Restricción de rol Operativa → Director
Probado el bypass más agresivo posible: con DevTools abierto en la instancia Operativa, se invocó `window.nicos.setRole('director')` directamente desde la consola, saltando la UI por completo. El proceso principal de Electron lo rechazó con el mensaje exacto implementado en `main.js` ("PC ya está configurada como Operativa..."). La restricción no depende de que la interfaz no ofrezca el botón -- está aplicada en el proceso principal, confirmado en runtime real, no solo por lectura de código.

### Recuperación tras reinicio (kill -9 real)
Se insertó una tarea real en estado `executing` con un intento `effect_started` sin entrada en el ledger externo (mismo escenario que `test_crash_recovery.py`, pero contra la instancia viva, no un test aislado). Se mató el proceso del sidecar con `kill -9` y se reinició con las mismas variables de entorno.

Resultado: la tarea se reconcilió sola a `needs_review` con veredicto `uncertain` -- visible en la Bandeja de tareas con el mensaje exacto y los 4 botones de resolución (`Confirmar ejecutada` / `Confirmar NO ejecutada y reintentar` / `Cancelar` / `Mantener en revisión`). Se probó "Cancelar" desde la UI real: confirmado en la base (`state = cancelled`) y en el log HTTP del sidecar (`POST /api/v1/tasks/.../resolve-execution 200`).

Durante esta verificación se investigó una falsa alarma (el botón parecía no responder en dos intentos) -- resultó ser el toggle de "Ver detalle" cerrándose por clicks repetidos propios, no un bug de la app. Confirmado con logs HTTP reales antes de descartarlo. **No se generó ningún hallazgo de rc5 por esto.**

### Exportación de logs sanitizados
Re-verificado (`scripts/exportar_logs_mac.sh`, ya endurecido en el ciclo anterior): sigue generando el reporte sanitizado correctamente, sin `nicos.db` completa por defecto.

### Visualización de ambas vistas
Confirmado visualmente: Resumen (datos reales de trading bot / consultorio), Tareas (Bandeja con la tarea recuperada), Ajustes del Director (tarjeta "Red — Tailscale", claves de IA, Google Sheets, dispositivos vinculados -- vacío tras el aislamiento correcto). Pantalla de pairing de Operativa. Ajustes de Operativa con secretos (sin secretos visibles) queda pendiente de la fase con Tailscale, porque esa pantalla solo se alcanza después de un pairing exitoso.

## Fase 2: prueba sobre Tailscale real (18/7/2026, misma Mac, IP `100.114.131.64`)

Con Tailscale activo y autenticado (ver incidente de arranque del daemon más abajo), se repitió la prueba con las dos instancias de prueba conectándose por la IP real de Tailscale en vez de localhost -- exactamente los 8 pasos que pidió Nicolás.

### Hallazgo 1 (bug real de rc4): `NICOS_JARVIS_TRABAJO` no se leía -- fuga de datos reales de producción a la sesión de prueba

Al abrir el tab "Resumen" de la instancia Director de prueba (con las 5 rutas financieras correctamente aisladas y verificadas por el script), se mostraron el PnL real del trading bot, estadísticas reales del consultorio y proyectos reales de cowork -- **datos de producción**, a pesar de que `run_test_director_mac.sh` exporta `NICOS_JARVIS_TRABAJO` apuntando a una carpeta de prueba vacía.

**Causa**: `sidecar/server.py` leía la variable de entorno `JARVIS_TRABAJO_PATH` (sin prefijo `NICOS_`, inconsistente con el resto del sistema), no `NICOS_JARVIS_TRABAJO`. El propio `sidecar/tests/test_integration_dry_run.py` ya asumía el nombre correcto (`NICOS_JARVIS_TRABAJO`), lo que confirma que el nombre usado en `server.py` era el error, no el script ni el test.

**Impacto**: de solo lectura (no se escribió nada en producción), pero rompía la garantía de aislamiento completo que esta ronda de pruebas existe para verificar.

**Corrección**: `sidecar/server.py` línea 66-68, cambiado el nombre de la variable leída de `JARVIS_TRABAJO_PATH` a `NICOS_JARVIS_TRABAJO` (mismo valor por defecto, la ruta real de producción, para no romper el comportamiento en producción). Verificado tras el fix: el tab Resumen muestra "Sin datos" en los tres bloques, como corresponde a una carpeta de prueba vacía.

### Hallazgo 2 (bug real de rc4): la UI del Director queda con el puerto del sidecar viejo tras "Guardar" en Ajustes

Siguiendo el flujo documentado y exactamente el que pidió Nicolás (Ajustes → pegar IP de Tailscale → Guardar), el sidecar se reinicia correctamente (nuevo puerto efímero, confirmado por log), pero la ventana Electron del Director nunca se entera del puerto nuevo. Consecuencia: "Vincular nuevo dispositivo" no hacía nada visible (el fetch fallaba en silencio contra un proceso que ya no existía) -- y en realidad **toda** la UI (Resumen, Tareas, Chat, listado de dispositivos) quedaba rota de la misma forma hasta recargar toda la ventana a mano. Se confirmó también que un simple Cmd+R no alcanza para arreglarlo (la URL recargada trae el puerto viejo en el query string) -- hace falta cerrar y reabrir el proceso completo.

**Causa**: `renderer/director/app.js` guarda el puerto del sidecar en una variable `API` fijada una sola vez al arrancar (`init()`); `renderer/shared/settings-panel-director.js` recibía ese mismo valor una sola vez como parámetro `apiBase`. Ninguno de los dos se actualizaba cuando `nicos:save-settings` reiniciaba el sidecar en un puerto nuevo -- a pesar de que el propio IPC ya devolvía el puerto nuevo (`{ok, port}`), ese valor de retorno se descartaba sin usar.

**Impacto**: alto -- rompe el flujo de primer uso documentado (cargar la IP de Tailscale y vincular a Marianela en la misma sesión), exactamente el camino que sigue cualquier usuario real la primera vez que configura la app.

**Corrección**:
- `renderer/shared/settings-panel-director.js`: el handler de "Guardar" ahora usa el `port` que devuelve `saveSettings()`, reasigna la variable local `apiBase` (compartida por los listeners de pairing/revocar ya definidos, mismo binding) y llama a un callback `onPortChange` si se le pasó uno. También refresca la lista de dispositivos tras guardar.
- `renderer/director/app.js`: nueva función `updateApiBase(newPort)` que actualiza `API` y vuelve a inicializar el tab de Tareas (`initTasksTab`); se pasa como callback a `renderSettingsPanelDirector`.

Verificado tras el fix: "Guardar" con un cambio de IP de Tailscale reinicia el sidecar, y en la misma sesión (sin recargar nada) "Vincular nuevo dispositivo" generó un código real y el listado de dispositivos se actualizó solo.

### Resultados de los 8 pasos (con las correcciones ya aplicadas)

1. **IP de Tailscale registrada**: `100.114.131.64` (confirmada con `tailscale ip -4`).
2. **Sidecar reiniciado con la interfaz disponible**: confirmado por log (`servidor de red (Tailscale) escuchando en 100.114.131.64:47500`), primero vía relanzamiento del script y después vía el flujo real de producción (Ajustes → Guardar).
3. **Bind verificado**: `lsof` confirma el sidecar escuchando solo en `127.0.0.1:<puerto efímero>` (Director local) y `100.114.131.64:47500` (Tailscale) -- nunca en `192.168.0.113` (LAN normal de la Mac) ni en `0.0.0.0`.
4. **Operativa configurada con la IP de Tailscale** (no localhost) en la pantalla de pairing.
5. **Pairing + tarea + visibilidad en tiempo real + revocación**: pairing completado por la red real (`POST /api/v1/pairing/start` y `/pairing/complete`, ambos 200, por la IP de Tailscale). Tarea creada desde Operativa visible instantáneamente en la Bandeja del Director, con el nombre del dispositivo (`marianela`) y timestamp correctos. Sin claves de IA cargadas, la tarea termina en estado `Falló` en la clasificación -- comportamiento esperado y correcto (no hay forma de llegar a `pending_approval` sin IA real); aprobar/rechazar/pedir información ya se habían probado exhaustivamente contra la máquina de estados real en la fase sin red (mismo código HTTP, no hace falta repetir la lógica, solo el transporte). Revocación probada desde Ajustes del Director ("Revocar" → estado pasa a "Revocado"); confirmado que Operativa pierde acceso de inmediato -- el siguiente intento de listar tareas devuelve "token inválido o ausente".
6. **Outbox con el sidecar detenido**: tarea enviada desde Operativa con el sidecar apagado quedó en cola ("1 en espera", mensaje "El ejecutor está desconectado"); al reiniciar el sidecar, el flush automático (cada 30s) la reenvió sola, sin duplicados -- confirmado también contra la base (`SELECT` sobre `tasks` muestra exactamente 2 filas para las 2 tareas de prueba enviadas, no 3).
7. **Desconexión de Tailscale**: `tailscale down` -- confirmado con `curl` que el sidecar deja de responder tanto en la IP de Tailscale como en la IP LAN normal de la Mac (`192.168.0.113`) -- no existe ningún fallback a la LAN común. Reconectado con `tailscale up`; el sidecar volvió a responder solo, sin necesidad de reiniciarlo.
8. **Smoke test de Claude/OpenAI**: pendiente -- requiere que Nicolás cargue sus propias API keys en la instancia Director de prueba, como ya había acordado.

## Fase 3: smoke test real de Claude y OpenAI (18/7/2026, sobre v0.2.1-rc5)

Nicolás cargó personalmente sus API keys reales de Anthropic y OpenAI en Ajustes de la instancia Director de prueba (nunca vistas, leídas ni logueadas por Claude en ningún momento -- solo se confirmó el indicador "ya configurada ✓" de la UI). Ejecución financiera bloqueada durante toda la prueba con una guarda temporal en `worker.py` (`NICOS_TEST_DISABLE_EXECUTION=1`): la clasificación corría completa contra las APIs reales, pero `execute_action()` nunca se llamó -- ninguna tarea, en ningún caso, llegó a ejecutarse. Cada tarea se canceló explícitamente después de inspeccionarla.

### Test 1 -- Claude: éxito completo
Mensaje: gasto ficticio de librería para Abate, $12.345, 18/07/2026. Extracción correcta (`domain: abate, intent: register_expense, amount: 12345, date: 18/07/2026, concept: Librería`), proveedor confirmado `claude` (forzado vía `provider_matrix.json`), latencia ~6.4s (recibida→clasificada), sin secretos en logs. Tokens: no disponible -- el código no captura uso de tokens en ningún punto (ni para Claude ni OpenAI), señalado como gap menor. Cancelada antes de cualquier ejecución.

### Test 2 -- OpenAI: cuenta sin crédito, fallback automático a Claude
La extracción con OpenAI falló con `429 insufficient_quota` (cuenta de OpenAI sin billing cargado -- no es un bug de la app). El sistema cayó automáticamente a Claude (`fallback_allowed_for_simple_tasks: true`) y completó la extracción igual (`domain: cfo, intent: register_income, amount: 23456, date: 18/07/2026, concept: Devolución de una compra`). Se agregó un log de diagnóstico temporal en `ai_router.py` para capturar el error real de OpenAI sin exponer la key; confirmado y revertido después.

### Casos negativos

**1. Clave inválida**: probado a nivel de código (env var sintética inválida en un subproceso aislado, nunca la key real de Nicolás). Resultado limpio: `401 authentication_error`, tarea → `failed`, sin crash del worker, sin secretos en el mensaje de error.

**2. Respuesta malformada -- reveló un bug real y severo, no relacionado con IA malformada en sí**: al simular distintas formas de dato malformado (dict vacío, `data` no-dict, `amount` con tipo incorrecto), se encontró que **cualquier tarea cuyo dominio resulte ambiguo o fuera de alcance (`domain: "unknown"`, un valor legítimo del propio schema de extracción) queda atascada para siempre en el estado `parsing`**, reintentando cada 1 segundo con llamadas reales a la IA, sin ningún mensaje de error visible para el Director. Confirmado también con una llamada real (no simulada): el mensaje "Cambié la posición del trading bot en BTC..." (deliberadamente fuera de alcance) reprodujo el mismo bug con Claude real.

Causa raíz en `worker.py::_process_classification`: cuando `classify_request()` devuelve `None` (dominio ambiguo), el código intenta `tasks.transition(task_id, "needs_information", ...)` estando la tarea todavía en estado `parsing` -- pero `ALLOWED_TRANSITIONS["parsing"]` solo permite `{"classified", "failed", "cancelled"}`, nunca `needs_information`. Esto dispara `InvalidTransition`, capturada por el `except` externo, que intenta la red de seguridad `needs_review` -- **tampoco alcanzable desde `parsing`** -- y esa segunda excepción se descarta en silencio (`except Exception: pass`). La tarea queda huérfana en `parsing`, que sigue en `QUEUED_STATES`, así que el worker la vuelve a tomar y reintenta la extracción real cada segundo, indefinidamente, quemando llamadas a la API sin que nadie se entere.

Las 5 tareas afectadas (3 simuladas + 1 real + la del caso 3 de abajo) se cancelaron manualmente para detener el loop en cuanto se detectó.

**3. Caída de proveedor / alcance del fallback**: con OpenAI caído por cuota, se probó también con un mensaje que hubiera requerido aprobación (`"Autorizar un pago nuevo..."`, intent `new_financial_action`, no un `logging_intent`). El fallback a Claude se activó igual que en el test 2 -- confirma que el fallback de `extract()` **no está realmente condicionado a que la tarea sea "verde"**, como sugiere el nombre `fallback_allowed_for_simple_tasks`: ocurre siempre que el proveedor primario falla, sin importar el riesgo final de la tarea, porque el riesgo recién se calcula DESPUÉS de que la extracción tuvo éxito -- estructuralmente no puede conocerse antes. Ese intento particular también cayó en el bug del caso 2 (dominio ambiguo), así que la prueba de "extracción totalmente fallida → needs_review" quedó cubierta por el caso 1 (clave inválida): el resultado real es `failed`, nunca `needs_review`, contradiciendo la expectativa de Nicolás/ChatGPT.

### Cierre de la fase

- Guarda temporal de `worker.py` y diagnóstico temporal de `ai_router.py` revertidos.
- Suite completo (16/16) vuelto a correr: sin regresiones.
- `git diff` vacío confirmado.
- `HEAD` confirmado igual al commit de `v0.2.1-rc5` (`b94a11310b7207f2f43901657e6729b2d4eafb02`).
- Ningún archivo productivo modificado; ninguna acción financiera ejecutada en ningún momento de la fase.

## Fase 4: corrección del bug bloqueante (v0.2.1-rc6, 18/7/2026)

Nicolás decidió corregir el bug del caso 2 de inmediato en vez de dejarlo solo documentado ("es un bug bloqueante para release"), con una especificación detallada de 8 requisitos. Resumen de lo que cambió:

**1. Máquina de estados (`tasks.py`)**: `ALLOWED_TRANSITIONS["parsing"]` ahora permite `needs_information` y `needs_review` (además de `classified`/`failed`/`cancelled`). También se agregó `needs_review` a `ALLOWED_TRANSITIONS["received"]` -- hallado por el propio test del límite de intentos: si una tarea nunca llega a `parsing` (crash muy temprano, repetido), el respaldo de intentos necesitaba poder cortar desde ahí también. Y `needs_information` se agregó a su propio conjunto de destinos (self-loop), para el caso "Nicolás aportó info pero todavía no alcanza".

**2. Resiliencia de extracción (`ai_router.py`, reescrito)**: `extract()` ahora tiene un presupuesto duro de **2 llamadas HTTP reales como máximo** por invocación, clasifica el tipo de falla (`auth_error` vs `transient_error` vs `malformed`, por nombre de clase de excepción -- Anthropic y OpenAI exponen los mismos nombres) y valida la forma estructural de cualquier respuesta antes de aceptarla como éxito. Política: error transitorio o de cuota → un intento con el alternativo; malformado → un intento con el alternativo (mismo presupuesto); auth/config inválida → error visible, sin probar el alternativo (para no enmascarar una key mal configurada). `domain: "unknown"` ya NO es un fallo de extracción -- es un resultado `'success'` estructuralmente válido, y la ambigüedad de negocio se resuelve después, donde siempre debió resolverse (`centro_mando_adapter.classify_request`).

**3. `worker.py` reescrito**: `_process_classification` ahora maneja explícitamente los 3 outcomes de `extract()` más 2 casos nuevos (dominio ambiguo, campos insuficientes vía `REQUIRED_FIELDS_CFO`) -- cada uno con una transición válida, un `task_event` con detalle, y un `error_message` legible. Se agregó `extraction_attempts` (persistido en la tarea, migración `004_extraction_resilience.sql`) con `MAX_EXTRACTION_ATTEMPTS = 2`: ninguna tarea puede pasar por clasificación más de 2 veces, verificado ANTES de llamar a cualquier proveedor -- respaldo estructural independiente del arreglo de la máquina de estados. Nueva función `provide_missing_info()`: el Director completa a mano el dato faltante (típicamente el dominio) sin ningún llamado nuevo a IA, reevaluando de forma 100% determinística.

**4. Renombrado (`provider_matrix.json`)**: `fallback_allowed_for_simple_tasks` → `fallback_on_primary_failure`, con una nota explicando por qué el nombre viejo prometía algo que el código nunca podía cumplir (el riesgo se calcula después de la extracción, nunca antes).

**5. UI (`tasks-tab.js`)**: una tarea `needs_information` ahora muestra un selector de dominio + botón "Completar y reclasificar", además de "Cancelar tarea". Nueva ruta `POST /api/v1/tasks/:id/provide-info` (Director-only, mismo patrón que aprobar/rechazar).

**6. Hallazgo adicional durante la verificación en vivo**: `provide_missing_info()` no tenía su propio manejo de excepciones -- si `prepare_action()` rechazaba la combinación dominio+intent (ej. `intent: "other"`, todavía no soportado para `cfo`), la tarea quedaba parada en `classified` sin transición final. Corregido envolviendo esa cola en su propio try/except dentro de `_finish_classification()`, con test de regresión agregado (`test_completar_con_combinacion_no_preparable_termina_en_needs_review_no_en_classified`).

### Pruebas nuevas (9 archivos, todos en `sidecar/tests/`)

`test_extraction_domain_unknown.py`, `test_extraction_malformed_json.py`, `test_extraction_both_providers_fail.py`, `test_extraction_fallback_success.py`, `test_extraction_attempt_limit.py`, `test_worker_restart_no_reactivated_loop.py`, `test_needs_information_completion.py`, `test_no_extra_calls_after_terminal_state.py`, y `test_regression_rc5_parsing_stuck_bug.py` (revierte deliberadamente `ALLOWED_TRANSITIONS["parsing"]` al conjunto viejo dentro del propio test y confirma que el bug original reaparece -- si alguien revierte el fix de verdad en el futuro, este archivo empieza a fallar solo). Todas usan clientes falsos de Anthropic/OpenAI (`_fake_ai_clients.py`) -- ninguna pega a una API real.

Durante la escritura de los tests se encontró y corrigió un segundo gap real (no el bug original): `ALLOWED_TRANSITIONS["received"]` no incluía `needs_review`, así que el respaldo de `MAX_EXTRACTION_ATTEMPTS` podía fallar si la tarea nunca había llegado a `parsing`. Corregido antes de seguir.

### Verificación

- Suite completo: **25/25** (16 previos + 9 nuevos), sin regresiones, incluye `test_risk_policy_characterization.py` (los 10 casos reales) y `test_integration_dry_run.py` intactos.
- Smoke test real (no simulado) contra la instancia Director de prueba, con las API keys reales de Nicolás ya cargadas: se reenvió el mensaje exacto que reprodujo el bug original ("Cambié la posición del trading bot en BTC...") -- resultado: `needs_information` en el primer intento, `extraction_attempts: 1`, mensaje de error claro. Ventana de observación de 20 segundos sin ninguna llamada adicional (solo polling normal de la UI).
- Probado en vivo el flujo completo de `provide_missing_info` vía la ruta HTTP real: un mensaje genuinamente ambiguo ("Pagué $7.500 de insumos de librería, no sé si va para Abate o para mí") se completó en dos pasos (dominio, después fecha faltante) hasta llegar a `ready` / risk `simple` -- confirmando también que el dominio `abate` nunca se auto-ejecuta (diseño preexistente, sin cambios), terminando en `needs_review` sin tocar ningún archivo real.
- `git diff` vacío confirmado antes de este commit; ningún archivo productivo tocado; ninguna acción financiera ejecutada en ningún momento de la fase.

## Cierre general (rc5 + rc6)

No se tocó "Acerca de NicOS" (no existe todavía) -- queda para `v0.2.1-rc7`, en el momento del empaquetado, junto con cualquier otra corrección pendiente. `OpenAI 429 insufficient_quota` sigue siendo un problema de cuota/configuración de la cuenta de Nicolás, no un bug de NicOS -- documentado como tal en ambas fases.
