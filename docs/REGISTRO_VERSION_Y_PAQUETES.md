# Registro de versión y paquetes — prueba física v0.2.1-rc9

Completar durante la sesión física. Los tags `rc3`-`rc8` no se mueven -- son etapas previas ya cerradas (rc8 quedó aprobado como candidata Mac cerrada). `v0.2.1-rc9` es el commit exacto sobre el que debe correr esta sesión de Windows: agrega la corrección del workflow de empaquetado (`nicos-sidecar.spec` en vez de un comando `pyinstaller` armado a mano, que reproducía el bug de rc7 de migraciones nunca incluidas), sin ningún cambio de lógica operativa sobre lo ya aprobado en rc8.

## Commit de referencia

- Tag: `v0.2.1-rc9`
- Commit: `826325e`
- Verificar en el momento de arrancar la sesión (en ambas máquinas, si se copió el código a Windows):
  ```bash
  git log --oneline -1
  git describe --tags
  ```
  Los dos deben coincidir en la Mac y en la copia usada en la PC de Marianela — si no coinciden, **parar y resincronizar antes de seguir**, no continuar con código sin identificar.
- Confirmar que el tag no fue publicado en ningún remoto antes de reutilizarlo: `git remote -v` debe estar vacío (repositorio 100% local en esta etapa). Si en algún momento se agrega un remoto y se hace push, cualquier tag ya publicado pasa a ser inmutable -- una corrección posterior exige `rc10`, nunca reescribir un tag ya empujado.

## Paquetes instalados (completar durante la sesión)

### Mac (ya generado en esta Mac, Fase 7/rc9)

```bash
shasum -a 256 "dist/NicOS-Desktop-0.2.1-rc.9-arm64.dmg"
```

| Campo | Valor |
|---|---|
| Archivo | `NicOS-Desktop-0.2.1-rc.9-arm64.dmg` |
| SHA-256 | `9326a1b02c19064c8d0c73fa939c4b6d99e6982cd6a19ecaf6b160fe11443673` |
| Generado a partir del commit | `826325e` (`git_dirty: false`) |
| Fecha/hora de instalación | 19/7/2026, build 03:25:45 UTC -- instalado en `/Applications/NicOS Desktop.app` de esta Mac |

### Windows (`.exe`, paso 2-4 del bloque Windows)

En PowerShell, en la PC de Marianela, después de `npm run dist:win` (ver `docs/GUIA_PRUEBA_FISICA_v0.2.1.md`, sección 3, Opción B -- usar **exactamente** `pyinstaller nicos-sidecar.spec`, no un comando armado a mano):
```powershell
Get-FileHash "dist\NicOS-Desktop-0.2.1-rc.9-<arch>.exe" -Algorithm SHA256
```

| Campo | Valor |
|---|---|
| Archivo | |
| SHA-256 | |
| Generado a partir del commit | |
| Fecha/hora de instalación | |

Verificar además, leyendo `build-info.json` embebido (vía "Acerca de NicOS" en la app instalada, o `Contents\Resources\build-info.json` del paquete):

| Campo esperado | Valor esperado | Confirmado |
|---|---|---|
| Versión | `0.2.1-rc.9` | |
| Commit | `826325e` | |
| Edición | Operativa (Marianela) | |
| Plataforma/arquitectura | `win32` / (según la PC) | |
| Hash de `risk_policy.yaml` | `80aae8f444c65605f3c413c01ec326dce7d1bdd9a7feb91e2f0dccb1e0b3847d` (debe coincidir con el de Mac) | |

## Checklist físico (completar durante la sesión -- ver detalle de cada paso en `GUIA_PRUEBA_FISICA_v0.2.1.md`)

| # | Ítem | Resultado | Notas |
|---|---|---|---|
| 1 | Primer arranque limpio | | |
| 2 | Tailscale (Mac + Windows conectados, ping en ambos sentidos) | | |
| 3 | Pairing Mac <-> Windows | | |
| 4 | Tarea creada desde Windows llega a la Bandeja | | |
| 5 | Pedir información (`request-info`) refleja el cambio en Windows | | |
| 6 | Aprobación / rechazo (solo desde Mac, nunca desde Windows) | | |
| 7 | Revocación de dispositivo | | |
| 8 | Outbox con la Mac desconectada | | |
| 9 | Reenvío del outbox sin duplicar la tarea | | |
| 10 | Reinicio de Windows (outbox sobrevive, sin duplicar al reconectar) | | |
| 11 | Ausencia de secretos y rutas administrativas (Ajustes Operativa, `/api/v1/pairing/start` y `/api/v1/devices` devuelven 403 por red) | | |
| 12 | Desinstalación y reinstalación (perfil, pairing repetido sin estado corrupto) | | |

## Logs exportados

- [ ] Mac: `bash scripts/exportar_logs_mac.sh` -- carpeta generada: _______________
- [ ] Windows: `powershell -ExecutionPolicy Bypass -File scripts\exportar_logs_windows.ps1` -- carpeta generada: _______________

## Notas de la sesión

(Espacio libre para anotar cualquier desvío respecto al checklist, hallazgos, o decisiones tomadas en el momento — para que quede trazado junto con la versión exacta sobre la que se probó.)
