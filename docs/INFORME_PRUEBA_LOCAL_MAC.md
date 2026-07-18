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

## Pendiente (bloqueado por Tailscale y por las API keys de Nicolás)

No hecho en esta prueba, requiere que Nicolás actúe primero:

- Pairing real Mac↔Mac (Operativa simulada) por Tailscale.
- Flujo completo de tareas por red: crear desde Operativa, aprobar/rechazar/pedir información desde Director, confirmar actualización de estados en ambas vistas.
- Revocación de dispositivo y confirmación de que deja de poder acceder.
- Outbox con el sidecar detenido, reenvío automático al reconectar, sin duplicación.
- Desconexión de Tailscale, confirmar que no hay fallback a la LAN común.
- Smoke test real de Claude y OpenAI (texto administrativo ficticio, extracción/clasificación real, cancelación final sin ejecutar nada) -- requiere que Nicolás cargue sus API keys en la instancia Director de prueba.

No se tocó "Acerca de NicOS" (no existe todavía) -- queda para cuando se incorpore en rc5, junto con cualquier corrección que surja de las pruebas pendientes.
