# Registro de versión y paquetes — prueba física v0.2.1-rc4

Completar durante la sesión física. No mover el tag `v0.2.1-rc3` (queda como el commit del fix de reconciliación en aislamiento); `v0.2.1-rc4` es el commit que además incluye la guía de prueba física, los exportadores de logs sanitizados, y la corrección del empaquetado local en Windows — es el commit exacto sobre el que debe correr la sesión.

## Commit de referencia

- Tag: `v0.2.1-rc4`
- Verificar en el momento de arrancar la sesión (en ambas máquinas, si se copió el código a Windows):
  ```bash
  git log --oneline -1
  git describe --tags
  ```
  Los dos deben coincidir en la Mac y en la copia usada en la PC de Marianela — si no coinciden, **parar y resincronizar antes de seguir**, no continuar con código sin identificar.

## Paquetes instalados (completar durante la sesión)

### Mac (si se genera un `.dmg`, Opción B del paso 3 de la guía)

```bash
shasum -a 256 "dist/NicOS Desktop-<version>-arm64.dmg"
```

| Campo | Valor |
|---|---|
| Archivo | |
| SHA-256 | |
| Generado a partir del commit | |
| Fecha/hora de instalación | |

### Windows (`.exe`, Opción B del paso 3 de la guía)

En PowerShell, en la PC de Marianela, después de `npx electron-builder --win`:
```powershell
Get-FileHash "dist\NicOS Desktop Setup <version>.exe" -Algorithm SHA256
```

| Campo | Valor |
|---|---|
| Archivo | |
| SHA-256 | |
| Generado a partir del commit | |
| Fecha/hora de instalación | |

## Notas de la sesión

(Espacio libre para anotar cualquier desvío respecto al checklist, hallazgos, o decisiones tomadas en el momento — para que quede trazado junto con la versión exacta sobre la que se probó.)
