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
