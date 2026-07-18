# Informe de pruebas — NicOS Desktop v0.2.1-rc2

Este informe reemplaza a `REPORTE_PRUEBAS_v0.2.1-rc1.md` (que queda como registro histórico, sin editar) como release candidate vigente. Corrige un gap crítico que ChatGPT señaló al revisar rc1: el ledger de `operation_id` era de una sola fase (la sola presencia del ID se interpretaba como "ya escrito"), lo que no distingue "reservado, el proceso murió antes de escribir" de "escrito de verdad, el proceso murió antes de marcar committed". Ver la sección "Qué cambió respecto a rc1" más abajo para el detalle técnico completo.

## Metadata de la corrida

| Campo | Valor |
|---|---|
| Commit SHA (código + tests que este informe reporta) | `b875c82d1578233049235e9348666409a5153251` |
| Commit SHA (agrega este mismo informe encima del anterior — al que apunta el tag) | `70a7a06` |
| Tag `v0.2.1-rc2` apunta a | `70a7a06` (el commit que agrega este informe) |
| Rama | `feature/nicos-v0.2` (no fusionada a `main`) |
| Commit anterior (v0.2.1-rc1) | `15e14ef06067655b466104eda3873c361bfc4494` |
| OS | macOS 15.6.1 (Sequoia), build 24G90, Darwin 24.6.0, arm64 |
| Python (venv del sidecar) | 3.14.3 |
| Node.js | v25.8.2 |
| npm | 11.11.1 |
| Electron (declarado en package.json) | ^31.0.0 |
| Exit code de `run_all.py` | `0` |
| Duración total de la corrida | 4 segundos |
| Total de archivos de test | 16 |
| Resultado | 16/16 OK |
| Hash sha256 de `policies/risk_policy.yaml` | `80aae8f444c65605f3c413c01ec326dce7d1bdd9a7feb91e2f0dccb1e0b3847d` |
| `centro_mando_adapter.POLICY_VERSION` / `POLICY_HASH` | `1` / `80aae8f444c65605f3c413c01ec326dce7d1bdd9a7feb91e2f0dccb1e0b3847d` (coincide exacto con el hash del archivo, confirmado) |
| Hash sha256 de `sidecar/tests/fixtures/casos_17_07.json` | `aacae79eb2d5537ac6ff88461a1ea3d01ba62a43487045e4abcfe7e0496a9485` |
| `git status` final (working tree) | limpio -- sin cambios sin commitear |

## Resultado (salida real de `python3 sidecar/tests/run_all.py`)

```
[OK] test_clinical_guard.py
[OK] test_crash_recovery.py
[OK] test_db_backup_restore.py
[OK] test_effect_started_ordering.py
[OK] test_execution_reconciliation.py
[OK] test_integration_dry_run.py
[OK] test_invalid_policy_yaml.py
[OK] test_lan_admin_routes_403.py
[OK] test_operativa_permissions_403.py
[OK] test_policy_traceability.py
[OK] test_reconciliation_gap_v2.py
[OK] test_resolve_execution.py
[OK] test_risk_policy_characterization.py
[OK] test_stale_approval.py
[OK] test_task_flow_feature_flag.py
[OK] test_worker_atomic_claim.py

16/16 archivos de test pasaron.
```

## Qué cambió respecto a rc1 (el gap que señaló ChatGPT)

**El problema real**: `registrar_movimiento.py` reclamaba el `operation_id` (lo appendeaba a un archivo ledger) ANTES de escribir el movimiento. La reconciliación de rc1 interpretaba "el operation_id está en el ledger" como "el movimiento se escribió" -- pero eso es falso si el proceso murió justo después de reclamar y antes de escribir: el ledger tendría el ID igual, sin que el movimiento existiera.

**La corrección**: el ledger externo pasa a ser JSONL de dos (o tres) estados por operación:
- `reserved` -- escrito ANTES de tocar `foto_financiera_*.md`, con el texto EXACTO de la fila que se va a insertar (`row_text`), calculado antes de escribir.
- `committed` -- escrito DESPUÉS de que la escritura tuvo éxito.
- `failed` -- cuando el script detecta limpiamente que no puede escribir (ej. no existe el archivo de destino) -- esto sí es certeza de que no hubo efecto.

La reconciliación (`centro_mando_adapter.reconcile_execution_attempt`) ahora es deliberadamente conservadora:
- **Nunca confía ciegamente en la palabra `committed`** -- siempre busca el `row_text` exacto dentro de los `foto_financiera_*.md` reales antes de declarar `effect_confirmed`. Si el ledger dice `committed` pero el texto no aparece en el archivo, el resultado es `uncertain`, no `effect_confirmed`.
- **`reserved` solo, sin evidencia en el archivo, ahora es `uncertain`** -- antes (rc1) esto se interpretaba como certeza de fallo (`effect_failed`); es un cambio deliberado hacia el lado conservador: ante la duda, pedir revisión humana en vez de asumir cualquier cosa con falsa certeza.
- **`reserved` solo, CON evidencia verificable en el archivo real, se reconcilia a `effect_confirmed`** -- este es el caso que motivó el hallazgo: el movimiento SÍ se escribió, el proceso murió antes de alcanzar a marcar `committed`, pero NicOS puede demostrarlo igual buscando el texto exacto en el archivo real.
- **`failed` explícito** (el script mismo detectó que no podía escribir) sigue siendo certeza de `effect_failed`.

## Los 3 escenarios exactos pedidos, con resultado real de la corrida

Todos en `sidecar/tests/test_reconciliation_gap_v2.py`, usando el `registrar_movimiento.py` real (cargado vía `importlib`, con las mismas funciones que usaría en producción) contra archivos temporales.

| Escenario | Test | Resultado esperado | Resultado obtenido |
|---|---|---|---|
| 1. Kill después de reservar, antes de escribir | `test_escenario_1_kill_tras_reservar_antes_de_escribir` | movimiento ausente; ledger `reserved`; tarea `uncertain`; nunca `completed` | ✅ Confirmado: `execution_attempts.status == 'uncertain'`, tarea en `needs_review`, nunca `completed` |
| 2. Kill después de escribir, antes de marcar committed | `test_escenario_2_kill_tras_escribir_antes_de_committed` | reconciliar inspeccionando el efecto real; `completed` solo si puede demostrarse | ✅ Confirmado: ledger seguía en `reserved`, pero el `row_text` SÍ estaba en el archivo real -- reconciliado a `completed` / `effect_confirmed` |
| 2b (contraste, no pedido pero agregado) | `test_escenario_2b_contraste_sin_evidencia_en_el_archivo_es_uncertain` | si NO hay evidencia en el archivo, debe ser `uncertain`, no `completed` | ✅ Confirmado |
| 3. Kill después de committed, antes de que NicOS marque completed | `test_escenario_3_kill_tras_committed_antes_de_marcar_completed` | reconciliación automática a `completed`; ninguna reejecución | ✅ Confirmado: `completed` automático, un solo `execution_attempts` por tarea (sin reintentos), el movimiento aparece UNA sola vez en el archivo (sin duplicación) |

## Verificación de orden: `effect_started` antes del subprocess

`test_effect_started_ordering.py` confirma con un espía en `subprocess.run` que el estado `effect_started` ya está persistido y commiteado en `execution_attempts` en el momento exacto en que se invocaría el subprocess real -- cerrando la ventana que señaló la revisión ("si el subprocess se inicia antes de guardar ese estado, existe una ventana donde la ejecución ocurrió pero NicOS cree que quedó únicamente claimed"). Se verificó además que el test detecta la regresión: se invirtió el orden a propósito, el test falló como se esperaba, y se revirtió el cambio (ver el commit para el detalle de esa verificación).

## Mapa completo: los 9 puntos de la revisión original → estado actual

(Sin cambios respecto a rc1 salvo el punto 1, ver `REPORTE_PRUEBAS_v0.2.1-rc1.md` para la tabla completa de los 9 puntos.)

| # | Punto | Estado en rc2 |
|---|---|---|
| 1 | operation_id asociado a un recibo verificable | **Corregido en rc2** -- ledger de 2 fases + verificación real contra el archivo, ver arriba |
| 2-9 | (sin cambios respecto a rc1) | Igual que en `REPORTE_PRUEBAS_v0.2.1-rc1.md` |

## Tests afectados por el cambio de formato del ledger (actualizados, no solo agregados)

- `test_execution_reconciliation.py`: reescrito para las nuevas semánticas conservadoras (10 tests).
- `test_integration_dry_run.py`: una sola aserción ajustada -- el ledger ahora tiene 20 líneas para 10 operaciones exitosas (reserved + committed cada una), pero sigue habiendo 10 `operation_id` únicos. El resto del test (incluida la verificación de que no se tocan archivos reales) no cambió.
- `test_crash_recovery.py`: sin cambios -- su escenario (`claimed` puro, subprocess nunca invocado) no pasa por el ledger externo en absoluto, no se ve afectado por este cambio.

## Explícitamente NO hecho en esta etapa (sigue bloqueado hasta que Nicolás esté presente)

- Verificación visual con capturas de pantalla reales de la app Electron.
- Pairing físico real entre esta Mac y una segunda máquina por Tailscale.
- Cualquier carga productiva nueva contra `foto_financiera_*.md` real.
