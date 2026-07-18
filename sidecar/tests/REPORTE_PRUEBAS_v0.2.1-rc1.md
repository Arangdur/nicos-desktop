# Informe de pruebas — NicOS Desktop v0.2.1-rc1

Generado corriendo `python3 sidecar/tests/run_all.py` de verdad (salida real, no reconstruida a mano). Ningún test toca `Centro de Mando/` ni `CFO y Decisiones Estrategicas/` reales — todos usan variables de entorno (`NICOS_DB_PATH`, `NICOS_CFO_DIR`, `NICOS_REGISTRO_ENTRADA`, `NICOS_OPERATION_LEDGER`, `CENTRO_DE_MANDO_DIR`, `NICOS_JARVIS_TRABAJO`) para redirigir toda escritura a archivos temporales.

## Resultado

```
[OK] test_clinical_guard.py
[OK] test_crash_recovery.py
[OK] test_db_backup_restore.py
[OK] test_execution_reconciliation.py
[OK] test_integration_dry_run.py
[OK] test_invalid_policy_yaml.py
[OK] test_lan_admin_routes_403.py
[OK] test_operativa_permissions_403.py
[OK] test_policy_traceability.py
[OK] test_resolve_execution.py
[OK] test_risk_policy_characterization.py
[OK] test_stale_approval.py
[OK] test_task_flow_feature_flag.py
[OK] test_worker_atomic_claim.py

14/14 archivos de test pasaron.
```

## Mapa: los 9 puntos de la revisión de ChatGPT → qué test lo prueba

| # | Punto | Test(s) | Qué prueba concretamente |
|---|---|---|---|
| 1 | operation_id asociado a un recibo verificable, no solo al ledger SQLite | `test_execution_reconciliation.py` | `test_efecto_SI_ocurrio_pero_nicos_murio_antes_de_registrar_resultado`: el subprocess escribió de verdad (operation_id en el ledger real de `registrar_movimiento.py`) pero el proceso murió antes de `finish_execution_attempt()` — al reiniciar, la tarea se reconcilia sola a `completed`, sin intervención humana |
| 2 | Estados del ledger: claimed / effect_started / effect_confirmed / effect_failed / uncertain | `test_execution_reconciliation.py` (5 tests de `reconcile_execution_attempt` en aislamiento), `test_crash_recovery.py` | Los 5 estados existen como `CHECK` en la migración 003 y cada transición se ejercita: `claimed`→siempre `effect_failed` sin mirar el ledger; `effect_started`+presente→`effect_confirmed`; `effect_started`+ausente→`effect_failed`; ledger no leíble→`uncertain` |
| 3 | Flujo del Director para resolver `uncertain` (confirmar ejecutada / confirmar no ejecutada + reintentar / cancelar / mantener en revisión) | `test_resolve_execution.py` | Las 4 decisiones sobre `tasks.resolve_execution()`, incluyendo que "reintentar" genera un `operation_id` **nuevo**, nunca reutiliza el que falló |
| 4 | Rol Operativa no puede escalar permisos en el servidor, ni con token válido, config borrada o requests modificadas | `test_operativa_permissions_403.py` (8 tests, con un dispositivo pareado real y un token válido de verdad) | `approve`/`reject`/`request-info`/`resolve-execution`/`pairing/start`/`devices` (GET y revoke) devuelven 403 por red aunque el token sea válido; un test de contraste confirma que el mismo token SÍ puede crear tareas propias — el bloqueo es por rol, no por autenticación fallida |
| 5 | Worker atómico — dos sidecars no pueden ejecutar la misma tarea a la vez | `test_worker_atomic_claim.py` | Dos `WORKER_ID` compitiendo por la misma tarea `ready`: solo uno gana el `UPDATE...WHERE`; limpieza de locks huérfanos al reiniciar |
| 6 | Versión/hash de `risk_policy.yaml` guardados por tarea | `test_policy_traceability.py` | Una tarea clasificada por el flujo real (worker + IA stubeada) tiene `policy_version`/`policy_hash` poblados y coincidentes con `centro_mando_adapter.POLICY_VERSION`/`POLICY_HASH` (sha256 del archivo) |
| 7 | Guard clínico como defensa secundaria; barrera primaria = allowlist de dominio, bloqueo antes de cualquier proveedor, logs sin datos sensibles | `test_clinical_guard.py` (heurística en aislamiento) + estructura del código: `clinical_guard.detect_clinical_data()` corre en `POST /api/v1/tasks` ANTES de crear la tarea — `ai_router.extract()` solo se invoca después, de forma asíncrona, desde el worker. Un texto bloqueado nunca genera una fila en `tasks`, así que nunca puede llegar a un proveedor de IA. Grep confirmó que `raw_text` nunca se imprime a stderr/stdout |
| 8 | ACL de Tailscale — `pairing/start` no accesible remotamente | `test_operativa_permissions_403.py::test_pairing_start_bloqueado_por_red_incluso_sin_token` + `test_lan_admin_routes_403.py` + documentación de ACL en `README.md` | Bloqueado a nivel de aplicación (no depende de la ACL), más ACL de ejemplo documentada como capa adicional |
| 9 | Informe de pruebas reproducible | Este archivo + `run_all.py` | Cubre: 10 fixtures sin tocar producción (`test_integration_dry_run.py`), idempotencia (`test_integration_dry_run.py` + `test_resolve_execution.py`), caída y recuperación (`test_crash_recovery.py`, `test_execution_reconciliation.py`), aprobación invalidada por cambio de revisión/hash (`test_stale_approval.py`, 5 tests incluyendo el escenario real de reclasificación), permisos de Operativa (`test_operativa_permissions_403.py`), migración+integrity_check+backup+restauración (`test_db_backup_restore.py`), política YAML inválida (`test_invalid_policy_yaml.py`), ausencia de modificaciones en archivos productivos (`test_integration_dry_run.py::test_no_toca_archivos_reales`, con hash sha256 antes/después) |

## Hallazgo importante durante esta etapa (no pedido por ChatGPT, encontrado al implementar el punto 3)

Al construir la ruta `resolve-execution`, se encontró un **bug preexistente real** en `server.py`: `_handle_task_action` (usado por `approve`/`reject`/`request-info`) y el revoke de dispositivos extraían el ID de la URL con `path.split("/")[3]`, que en realidad devuelve el literal `"tasks"` o `"devices"`, no el ID — un error de índice (debía ser `[4]`). Confirmado con `git show v0.2-tareas-aprobacion:sidecar/server.py` que el bug existía desde el primer tag de v0.2, antes de cualquier trabajo de esta sesión. Corregido, y verificado con una llamada HTTP real de punta a punta (`POST /api/v1/tasks/<id>/approve` con un hash y revisión reales) confirmando que ahora aprueba la tarea correcta.

## Gaps conocidos (para la próxima iteración, no bloqueantes para rc1)

- No se corrió ningún test contra la API real de Claude/OpenAI (todos usan `ai_router.extract()` stubeado) — es deliberado (costo, no-determinismo), pero significa que la integración real con el proveedor de IA no está cubierta por este informe.

## Explícitamente NO hecho en esta etapa (bloqueado hasta que Nicolás esté presente)

- Verificación visual con capturas de pantalla reales de la app Electron.
- Pairing físico real entre esta Mac y una segunda máquina por Tailscale.
- Cualquier carga productiva nueva contra `foto_financiera_*.md` real — la primera carga productiva de v0.2.1 se hace después de que Nicolás apruebe explícitamente, con un movimiento nuevo y chico, no con las 10 cargas del 17/07 (esas ya están en el archivo real desde antes de esta sesión).
