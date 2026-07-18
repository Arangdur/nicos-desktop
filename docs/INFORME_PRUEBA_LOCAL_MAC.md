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

## Pendiente

- Smoke test real de Claude y OpenAI (texto administrativo ficticio, extracción/clasificación real, cancelación final sin ejecutar nada) -- requiere que Nicolás cargue sus API keys en la instancia Director de prueba.

No se tocó "Acerca de NicOS" (no existe todavía) -- queda para cuando se incorpore en rc5 (o el número de rc que corresponda una vez tageados los fixes de esta fase), junto con cualquier otra corrección pendiente antes del empaquetado.
