# NicOS Desktop

App instalable (Mac + Windows) con dos vistas — Director (Nicolás) y Operativa (Marianela) —
que integra Claude y OpenAI. v1, ver `/Users/nicolasbuso/.claude/plans/vast-growing-giraffe.md`
para el plan completo aprobado.

## Verificado en esta sesión (macOS, arm64)

- Sidecar Python standalone (`sidecar/server.py`): probado con `curl` — `/ping`, `/director/summary`
  (datos reales de `jarvis-trabajo/*.json`), `/whatsapp/messages` y `/director/chat` fallan
  controladamente cuando faltan credenciales, sin crashear.
- Binario compilado con PyInstaller (`sidecar/dist/nicos-sidecar`): mismo comportamiento que
  standalone, confirmado con `curl` directo al puerto.
- App en modo desarrollo (`npm start`): verificada visualmente — selector de rol, vista Director
  (Resumen con datos reales, Chat con manejo de error correcto, Ajustes), vista Operativa
  (Mensajes con manejo de error correcto, Ajustes), cambio de rol.
- App empaquetada (`dist/mac-arm64/NicOS Desktop.app`): confirmado que el sidecar embebido
  arranca y responde igual que en desarrollo.
- Instalador `.dmg` generado en `dist/NicOS Desktop-0.1.0-arm64.dmg` (129MB, sin firmar).

## Pendiente — requiere que Nicolás resuelva

1. **Cuenta de servicio de Google Cloud** — crearla y compartirle la hoja "Bot WhatsApp
   Consultorio" para que la vista Operativa pueda leer/escribir datos reales. Sin esto, la
   vista Operativa muestra el error controlado que ya viste en la verificación.
2. **API keys de Anthropic y OpenAI** — cargarlas en Ajustes para que el Chat del Director
   funcione con datos reales (hoy responde el error controlado esperado).
3. **Nombre real del tab dentro del Google Sheet** — `sidecar/sheets_client.py` asume que el
   tab se llama `"Mensajes"` (constante `TAB_NAME`). No se confirmó el nombre real contra el
   Sheet de producción — hay que verificarlo y ajustar esa constante si es distinto.
4. **Repositorio en GitHub** — el workflow de `.github/workflows/build-windows.yml` ya está
   escrito, pero no puede correr hasta que este proyecto tenga un repo real en GitHub (puede
   ser privado) y se le haga push. Recién ahí se puede generar y probar el instalador `.exe`
   en la PC de Marianela — **no se pudo verificar el build de Windows desde esta sesión en Mac**.
5. **Firma de código** — sin certificado de Apple Developer ni de Windows, ambos instaladores
   van a mostrar advertencias de "editor no verificado/desconocido" al instalarse. Aceptable
   para uso interno de dos personas (click derecho → Abrir, en Mac; "más información → ejecutar
   de todas formas" en Windows SmartScreen) — evaluar certificados pagos si se distribuye más
   ampliamente.

## Rollback (v0.2.1)

Si el flujo de tareas/aprobación (`/api/v1/tasks*`) causa un problema y hace falta
"apagarlo" rápido sin tocar código:

```bash
export NICOS_TASK_FLOW_ENABLED=false
```

Con esto, `/api/v1/tasks*` devuelve 503 con mensaje claro; `/director/summary` y
`/director/chat` (lo que ya funcionaba en v0.1) siguen andando igual.

Para un rollback de código más de fondo: `git checkout v0.1-freeze` o
`v0.2-tareas-aprobacion` según hasta dónde haya que volver (tags en este repo).

**Ningún procedimiento de rollback — ni el feature flag, ni `git checkout`, ni
restaurar un backup de `nicos.db`— revierte automáticamente un movimiento
financiero que ya se escribió en `foto_financiera_*.md` real.** Eso, si hace
falta deshacerlo, lo hace Nicolás a mano, usando `execution_attempts` y
`task_events` (dentro de `nicos.db`) como evidencia de qué se ejecutó, cuándo,
y con qué `operation_id`. Antes de cualquier migración nueva de esquema, `db.py`
ya hace un backup automático de `nicos.db` a `sidecar/backups/` — restaurar uno
de esos backups recupera el estado de auditoría/aprobaciones, pero tampoco
deshace efectos externos ya ocurridos.

## Red — Tailscale y ACL (v0.2.1-rc1)

**Garantía a nivel de aplicación (no depende de Tailscale ni de ninguna ACL)**:
el servidor de RED (el que escucha en la IP de Tailscale, usado por la PC de
Marianela) nunca registra `/api/v1/pairing/start` ni `/api/v1/devices*` ni
`/api/v1/tasks/*/approve|reject|request-info|resolve-execution` como rutas
alcanzables — `Handler._is_lan()` las bloquea con 403 sin importar si el
`Authorization: Bearer <token>` es válido. Esto es más fuerte que cualquier
ACL externa porque no depende de configuración: aunque la PC de Marianela
reinstale la app, borre su configuración, o alguien modifique las requests a
mano, esas rutas siguen sin existir del lado de la red. Ver
`sidecar/tests/test_operativa_permissions_403.py`.

**ACL de Tailscale, como capa adicional (defensa en profundidad)** — no es la
única garantía, pero limita a nivel de red quién puede siquiera intentar
llegar al puerto de NicOS (47500 por default), por si en el futuro se agrega
una ruta nueva sin el chequeo `is_lan()` de arriba. Ejemplo de política (se
configura en el admin console de Tailscale, no en este repo):

```json
{
  "tagOwners": {
    "tag:nicos-director": ["nicolas@ejemplo.com"],
    "tag:nicos-operativa": ["nicolas@ejemplo.com"]
  },
  "acls": [
    {
      "action": "accept",
      "src": ["tag:nicos-operativa"],
      "dst": ["tag:nicos-director:47500"]
    }
  ]
}
```

Pasos: taggear la Mac de Nicolás como `tag:nicos-director` y la PC de
Marianela como `tag:nicos-operativa` desde el admin console de Tailscale;
esta ACL hace que NINGÚN otro dispositivo de la red de Tailscale (aunque esté
en la misma cuenta) pueda alcanzar el puerto 47500, ni siquiera para intentar
un pairing con un código robado. El pairing sigue exigiendo además el código
de 6 dígitos + rate limiting (`pairing.py`) — la ACL y el código son capas
independientes, no una sustituye a la otra.

## Cómo correr en desarrollo

```bash
cd "/Users/nicolasbuso/Claude/Projects/NicOS Desktop"
npm install
cd sidecar && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt && cd ..
npm start
```

## Cómo generar el instalador de Mac de nuevo

```bash
cd "/Users/nicolasbuso/Claude/Projects/NicOS Desktop/sidecar"
source .venv/bin/activate
pyinstaller --onefile --name nicos-sidecar --distpath dist --workpath build --specpath . server.py
cd ..
npx electron-builder --mac dmg
```
