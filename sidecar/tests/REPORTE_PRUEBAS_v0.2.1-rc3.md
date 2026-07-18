# Informe de pruebas — NicOS Desktop v0.2.1-rc3

Reemplaza a `REPORTE_PRUEBAS_v0.2.1-rc2.md` (que queda como registro histórico) como release candidate vigente. Corrige un problema de precisión real que ChatGPT señaló al revisar rc2: la verificación buscaba el texto de la fila como *substring* en cualquier parte de `foto_financiera_*.md`, lo que puede dar un falso positivo si ya existía una fila idéntica (mismo concepto/monto/fecha/tipo) de una carga anterior no relacionada.

## Metadata de la corrida

| Campo | Valor |
|---|---|
| Commit SHA (código + tests que este informe reporta) | `00ef0cf` |
| Commit base sobre el que se hizo este trabajo | `0d0a67b79e69d1df1b3b98dd9129087c4946332b` (tag `v0.2.1-rc2`) |
| Rama | `feature/nicos-v0.2` (no fusionada a `main`) |
| OS | macOS 15.6.1 (Sequoia), build 24G90, Darwin 24.6.0, arm64 |
| Python (venv del sidecar) | 3.14.3 |
| Node.js | v25.8.2 |
| npm | 11.11.1 |
| Electron (declarado en package.json) | ^31.0.0 |
| Exit code de `run_all.py` | `0` |
| Duración total de la corrida | 5 segundos |
| Total de archivos de test | 16 |
| Resultado | 16/16 OK |
| Hash sha256 de `policies/risk_policy.yaml` | `80aae8f444c65605f3c413c01ec326dce7d1bdd9a7feb91e2f0dccb1e0b3847d` (sin cambios respecto a rc1/rc2) |
| Hash sha256 de `sidecar/tests/fixtures/casos_17_07.json` | `aacae79eb2d5537ac6ff88461a1ea3d01ba62a43487045e4abcfe7e0496a9485` (sin cambios) |

## Resultado

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

## Qué cambió respecto a rc2

**El problema real**: en rc2, la reserva guardaba `row_text` (el texto exacto de la fila) y la reconciliación buscaba ese texto como substring en `foto_financiera_*.md`. Si el texto aparecía, se confirmaba el efecto. Pero esto no distingue "esta operación escribió esa fila" de "esa fila ya estaba ahí por otra razón" — dos cargas idénticas (mismo concepto, monto, fecha y tipo) en momentos distintos producen el mismo `row_text`.

**La corrección**: la reserva ahora guarda evidencia de POSICIÓN, no solo de contenido:
- `target_file`, `pre_write_size`, `pre_write_hash` — estado del archivo antes de escribir.
- `expected_bytes`, `expected_offset` — la posición exacta en bytes donde va a insertarse la fila, calculada antes de escribir.

La escritura pasa a hacerse con bloqueo exclusivo (`fcntl.flock`) + `flush()` + `os.fsync()`, y se verifica el contenido exacto en `expected_offset` antes de marcar `committed`. La reconciliación (`centro_mando_adapter._verify_row_at_offset`) ahora exige coincidencia exacta en esa posición — no alcanza con que el texto aparezca en cualquier lugar del archivo.

**Por qué el offset sigue siendo válido más adelante**: cada inserción nueva va siempre justo antes del `\n---` de cierre de la tabla de movimientos, así que el offset de una fila ya insertada nunca se corre — las inserciones posteriores van más abajo. Esto se aprovechó para no necesitar reajustar offsets guardados si se cargan más movimientos después.

## Test que reproduce el hallazgo exacto de ChatGPT

`test_reconciliation_gap_v2.py::test_falso_positivo_por_fila_preexistente_NO_ocurre`: arma un archivo con una fila preexistente (de una carga anterior, no relacionada) idéntica a la que la operación en cuestión *intentaría* escribir. La operación reserva pero el proceso "muere" antes de escribir nada de esta operación específica. Con la verificación por offset (rc3): la tarea correctamente NO se confirma (`uncertain`, no `completed`).

**Verificado que el test tiene dientes**: se revirtió temporalmente `_verify_row_at_offset` a una búsqueda por substring (el comportamiento de rc2), se corrió el test, y falló exactamente como se esperaba (`'completed' == 'completed'` cuando debía ser distinto) — reproduciendo el bug real que señaló ChatGPT. Se restauró el fix inmediatamente después.

## Explícitamente NO hecho en esta etapa (sigue bloqueado hasta que Nicolás esté presente)

- Verificación visual con capturas de pantalla reales de la app Electron.
- Pairing físico real entre esta Mac y una segunda máquina por Tailscale.
- Cualquier carga productiva nueva contra `foto_financiera_*.md` real.
