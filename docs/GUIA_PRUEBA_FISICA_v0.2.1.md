# Guía de prueba física — NicOS Desktop v0.2.1-rc3

Preparado para la sesión en persona con Nicolás (Director, Mac) y Marianela (Operativa, PC Windows). Cubre: Tailscale en ambas máquinas, instalación en Windows, el checklist de 9 pasos aprobado, comandos de diagnóstico, exportación de logs, y desinstalación/revocación.

**Regla de esta etapa**: nada de esto toca `foto_financiera_*.md` real ni ningún archivo productivo. El smoke test de Claude/OpenAI (paso 9) requiere autorización explícita de Nicolás en el momento — no se dispara solo.

---

## 0. Antes de empezar

- [ ] Rama `feature/nicos-v0.2`, exactamente en el tag **`v0.2.1-rc4`** (`git describe --tags` debe decir `v0.2.1-rc4`, no un commit posterior sin identificar). `v0.2.1-rc3` sigue existiendo y no se mueve — marca el commit del fix de reconciliación en aislamiento; `v0.2.1-rc4` es el que además incluye esta guía, los exportadores sanitizados, y la corrección del empaquetado local en Windows. Ver `docs/REGISTRO_VERSION_Y_PAQUETES.md` para registrar el SHA y, más adelante, los hashes de los paquetes instalados.
- [ ] `npm install` corrido en la Mac (`cd "NicOS Desktop" && npm install`).
- [ ] Sidecar con su venv armado (`cd sidecar && python3 -m venv .venv && source .venv/bin/activate && pip install -r requirements.txt`).
- [ ] Confirmar que el suite de tests pasa antes de arrancar: `python3 sidecar/tests/run_all.py` → debe decir `16/16 archivos de test pasaron`.

---

## 1. Tailscale en la Mac (Director)

```bash
# Si no está instalado:
brew install tailscale
brew services start tailscale

# Login (pide confirmación en el navegador):
sudo tailscale up

# IP privada asignada a esta Mac:
tailscale ip -4
```

- [ ] `tailscale status` muestra esta Mac conectada (no "Stopped" ni "NeedsLogin").
- [ ] Anotar la IP que devuelve `tailscale ip -4` (empieza con `100.`) — va en Ajustes de NicOS.
- [ ] En NicOS Desktop → Director → Ajustes → tarjeta "Red — Tailscale" → pegar esa IP → Guardar. Esto reinicia el sidecar con `NICOS_TAILSCALE_IP` seteada.
- [ ] Confirmar que el sidecar quedó escuchando en Tailscale, no en la red doméstica (ver sección de diagnóstico más abajo — `lsof` no debe mostrar el puerto 47500 en ninguna IP que no sea `127.0.0.1` o la de Tailscale).

## 2. Tailscale en la PC de Marianela (Windows)

1. Descargar e instalar Tailscale para Windows: https://tailscale.com/download/windows
2. Iniciar sesión con la **misma cuenta** de Tailscale que la Mac (Nicolás comparte la invitación o las credenciales).
3. Verificar conexión: abrir el ícono de Tailscale en la bandeja del sistema → debe decir "Connected".
4. Anotar la IP de Tailscale de esta PC (no hace falta para el pairing, pero sirve para diagnóstico): clic derecho en el ícono → "This device" → IP.

- [ ] Desde la Mac, probar conectividad antes de instalar NicOS: `tailscale ping <IP de la PC de Marianela>` — debe responder. Si no responde, no seguir: el problema es de red, no de la app.
- [ ] Desde la PC de Marianela (si tiene una terminal a mano, PowerShell): `tailscale ping <IP de la Mac>` — mismo chequeo en el otro sentido.

## 3. Instalar NicOS Operativa en la PC de Marianela

No hay todavía un instalador `.exe` generado. Dos caminos — **para la primera prueba física, usar la Opción A**; la Opción B (paquete real) es obligatoria antes de fusionar a `main`, pero puede quedar para una segunda sesión.

### Opción A — Correr desde código fuente en la PC de Marianela (para esta primera prueba)

En la PC de Marianela:
1. Instalar [Node.js LTS](https://nodejs.org) (incluye npm).
2. Copiar la carpeta `NicOS Desktop` completa a esa PC (pendrive, AirDrop-alternativo, o carpeta compartida — **no hace falta copiar `node_modules/` ni `sidecar/.venv/`**, se generan de nuevo ahí).
3. En PowerShell, dentro de la carpeta copiada:
   ```powershell
   npm install
   npm start
   ```
4. Al abrir, elegir rol **"Operativa"** en el selector — esta PC nunca debe arrancar el sidecar Python (el rol Operativa no lo hace, es un chequeo ya verificado por `test_operativa_permissions_403.py` y por el propio `main.js`).

Ventaja de este camino para la primera prueba: permite depurar rápido y separa errores funcionales de errores de empaquetado.

- [ ] La app abre en la PC de Marianela sin errores.
- [ ] Selector de rol aparece la primera vez (si no aparece, alguien ya eligió un rol antes en esta PC — ver "Desinstalación" más abajo para resetear).

### Opción B — Generar el `.exe` real empaquetado (obligatorio antes de fusionar a `main`)

**No hace falta GitHub para esto.** `electron-builder` empaqueta localmente, en la misma PC Windows — no requiere subir el código a ningún lado. Directamente en la PC de Marianela, con la carpeta del proyecto copiada (mismo paso 2 de la Opción A):

```powershell
cd sidecar
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
pyinstaller --onefile --name nicos-sidecar --distpath dist --workpath build --specpath . server.py
cd ..
npm install
npx electron-builder --win
```

El instalador queda en `dist\*.exe`. Windows SmartScreen va a advertir "editor desconocido" (no está firmado) — "Más información" → "Ejecutar de todas formas".

*(Alternativa, si en algún momento se decide tener el proyecto en GitHub para CI: el workflow `.github/workflows/build-windows.yml` ya está escrito y hace exactamente estos mismos pasos en `windows-latest`, subiendo el `.exe` como artefacto. Requiere crear/usar un repo y autorizar el push — **no hacer esto sin confirmarlo explícitamente con Nicolás en el momento**, es una decisión suya, no algo para resolver de antemano.)*

**Por qué esto es obligatorio antes del merge, no opcional**: un empaquetado de Electron puede fallar de formas que `npm start` nunca muestra — rutas relativas rotas, el sidecar no encontrado en su ubicación empaquetada, permisos de Windows, firewall bloqueando el puerto de Tailscale, `safeStorage` sin proveedor de cifrado disponible en esa PC, archivos que quedaron afuera del build, actualización del outbox, inicio automático, o el propio antivirus/SmartScreen interfiriendo. Repetir como mínimo los pasos 4 (crear tarea → aprobar) y 7 (outbox desconectado) del checklist de abajo, pero contra el `.exe` instalado, no contra `npm start`.

- [ ] El `.exe` se generó sin errores.
- [ ] Instalado y abierto en la PC de Marianela, sin errores.
- [ ] Repetir el paso 5 (flujo de tareas) contra el paquete instalado.
- [ ] Repetir el paso 7 (outbox desconectado) contra el paquete instalado.

## 4. Pairing real Mac ↔ Windows

En la Mac (Director → Ajustes → "Vincular nuevo dispositivo"):
- [ ] Se genera un código de 6 dígitos, válido 5 minutos.

En la PC de Marianela (pantalla de pairing, primera vez que se elige rol Operativa):
- [ ] IP de Tailscale de la Mac (la del paso 1, empieza con `100.`) — **no** la IP de red local de la oficina.
- [ ] Puerto: dejar `47500` salvo que se haya cambiado `NICOS_LAN_PORT`.
- [ ] Código de 6 dígitos recién generado.
- [ ] Nombre de esta PC (ej. "PC Consultorio").
- [ ] Click "Vincular" → debe decir "Vinculado con éxito" y pasar sola a la pantalla principal de Operativa.

En la Mac, Ajustes → lista de dispositivos vinculados:
- [ ] Aparece el nuevo dispositivo con el nombre puesto, estado "Activo".

## 5. Probar el flujo de tareas desde Windows

Desde la PC de Marianela, pestaña "Entrada rápida":
- [ ] Escribir algo tipo `"Registrar pago de electricidad de Abate por $450.000"` y enviar.
- [ ] Aparece en "Mis últimas tareas enviadas" con estado "Recibida"/"Procesando".

En la Mac, Bandeja de tareas (Director):
- [ ] La tarea aparece ahí (puede tardar unos segundos — el worker clasifica cada 1s).
- [ ] Si el dominio quedó ambiguo, probar "Pedir información" y confirmar que en Windows se refleja el cambio de estado.
- [ ] Si llegó a "Esperando tu aprobación", probar **Aprobar** una vez y **Rechazar** otra (con una segunda tarea) — confirmar en ambos casos que el estado se actualiza en la vista de Windows también (puede requerir refrescar la pestaña "Entrada rápida" en Operativa).
- [ ] Confirmar que aprobar/rechazar/pedir-info **no se puede hacer desde Windows** — no hay ningún botón para eso en la vista Operativa (ya verificado por `test_operativa_permissions_403.py`, pero conviene confirmarlo visualmente).

## 6. Revocar el dispositivo

En la Mac, Ajustes → dispositivo vinculado → "Revocar":
- [ ] El dispositivo pasa a "Revocado" en la lista.

En la PC de Marianela:
- [ ] Cualquier acción nueva (mandar una tarea, listar mensajes) falla con un error de autenticación — el token ya no es válido.
- [ ] "Olvidar vinculación" en Ajustes de Operativa, volver a intentar el pairing con un código nuevo → debe funcionar de nuevo (nuevo `device_id`, nuevo token).

## 7. Outbox con la Mac apagada / sidecar detenido

1. Apagar el sidecar (cerrar NicOS Desktop en la Mac, o `NICOS_TASK_FLOW_ENABLED=false` si se quiere mantener el resto andando).
2. En la PC de Marianela, mandar una tarea nueva desde "Entrada rápida".
   - [ ] Queda en el outbox local (mensaje "La Mac no está disponible en la red en este momento" o similar, contador de "tareas en espera" sube).
3. Prender de nuevo el sidecar en la Mac.
   - [ ] Al ratito (el outbox reintenta solo), la tarea se envía y desaparece del contador de pendientes en Windows.
   - [ ] En la Mac, la tarea aparece UNA sola vez en la Bandeja — no duplicada (la `idempotency_key` generada del lado de Windows previene esto, ya cubierto por `tasks.create_task`'s `UNIQUE` constraint).

## 8. Caída de Tailscale — confirmar que no hay fallback a la LAN común

En la Mac:
```bash
sudo tailscale down
```
- [ ] Diagnóstico (ver sección 10 abajo): el servidor de red del sidecar debe quedar **completamente cerrado** — nada escuchando en el puerto 47500, ni en la IP de Tailscale (que ya no existe) ni en `0.0.0.0`.

En la PC de Marianela, intentar mandar una tarea:
- [ ] La app muestra un mensaje claro de que no puede conectar (no un error técnico críptico) — `operativa-client.js` ya distingue este caso.

Volver a levantar Tailscale (`sudo tailscale up`) antes de seguir con el resto de la prueba.

## 9. Verificación visual de seguridad

- [ ] Director → Ajustes: puede cargar/ver (enmascaradas) las API keys de Anthropic/OpenAI, credenciales de Google, y la IP de Tailscale.
- [ ] Operativa → Ajustes: **ningún** campo de secretos — solo dispositivo vinculado, host/puerto de la Mac, contador de outbox, botón "Olvidar vinculación". Comparar visualmente con `renderer/shared/settings-panel-operativa.js` (no debería haber ningún `<input type="password">` ni similar).
- [ ] Intentar (si hay alguna forma en la UI) cambiar de rol Operativa → Director: debe fallar. Si no hay ni siquiera un botón para eso en la interfaz, mejor todavía — significa que ni la UI lo ofrece, más allá de que el proceso principal también lo bloquee (`main.js`, `nicos:set-role`).
- [ ] Intentar acceder a una ruta administrativa manualmente desde la PC de Marianela (con herramientas de desarrollo si están disponibles, o pidiéndole a Nicolás que lo intente por curl usando el token real capturado) — `/api/v1/pairing/start` y `/api/v1/devices` deben devolver 403.

## 10. Comandos de diagnóstico

**En la Mac:**
```bash
# ¿Qué está escuchando en el puerto de red?
lsof -iTCP:47500 -sTCP:LISTEN

# Estado de Tailscale:
tailscale status
tailscale ip -4

# Ping a la PC de Marianela por Tailscale:
tailscale ping <IP-de-Marianela>

# Sidecar respondiendo localmente:
curl -s http://127.0.0.1:<puerto-local>/ping
# el puerto local lo elige el SO -- buscarlo en la consola donde corre npm start,
# línea "[sidecar] servidor LOCAL escuchando en http://127.0.0.1:<puerto>"

# Ver el estado de una tarea puntual (requiere el puerto local):
curl -s "http://127.0.0.1:<puerto-local>/api/v1/tasks?state=needs_review"
```

**En la PC de Marianela (PowerShell):**
```powershell
# Estado de Tailscale:
tailscale status
tailscale ip -4

# Ping a la Mac:
tailscale ping <IP-de-la-Mac>

# Probar el puerto de la Mac directamente (sin pasar por la app):
Test-NetConnection -ComputerName <IP-de-la-Mac> -Port 47500
```

## 11. Exportar logs de ambas máquinas

Ver `scripts/exportar_logs_mac.sh` (Mac) y `scripts/exportar_logs_windows.ps1` (Windows). **Por defecto generan un reporte SANITIZADO**, no una copia de la base — `nicos.db` puede tener texto original de tareas, importes, resultados financieros y otros datos operativos, así que no sale por defecto:

```bash
# Mac, reporte sanitizado (conteos por estado, IDs, timestamps, sin contenido):
bash scripts/exportar_logs_mac.sh

# Mac, si hace falta la copia ÍNTEGRA de nicos.db para depurar algo puntual
# (el script avisa antes de copiarla):
bash scripts/exportar_logs_mac.sh --include-database
```

```powershell
# Windows -- esta PC no tiene nicos.db (el sidecar solo corre en la Mac), y el
# script NUNCA copia el token del dispositivo ni el contenido del outbox
# (solo indica si están configurados y cuántos ítems hay en cola), ni siquiera
# con el flag de abajo -- eso es fijo, no opcional:
powershell -ExecutionPolicy Bypass -File scripts\exportar_logs_windows.ps1

# + log crudo de npm start, si hace falta (el script avisa antes):
powershell -ExecutionPolicy Bypass -File scripts\exportar_logs_windows.ps1 -IncludeRawLog
```

Ninguno de los dos reportes sanitizados (Mac/Windows) exporta por defecto: tokens o secretos, contenido del outbox, texto de tareas, montos o resultados, variables de entorno, excepciones sin redactar, ni rutas locales con el nombre de usuario del sistema (`lsof` del lado Mac recorta explícitamente la columna `USER` por este motivo).

Instrucciones completas dentro de cada script (comentario al principio).

Para capturar la salida de la app mientras se corre la prueba (recomendado, no es automático hoy):
```bash
# Mac:
npm start 2>&1 | tee ~/Desktop/nicos-mac-$(date +%Y%m%d-%H%M).log

# Windows (PowerShell):
npm start 2>&1 | Tee-Object -FilePath "$env:USERPROFILE\Desktop\nicos-windows-$(Get-Date -Format yyyyMMdd-HHmm).log"
```

## 12. Smoke test real de Claude y OpenAI (requiere autorización explícita en el momento)

**No se dispara nada de esto sin que Nicolás lo autorice ahí mismo** — usa las API keys reales y genera costo, aunque sea mínimo.

1. Confirmar que las API keys de Anthropic y OpenAI están cargadas en Ajustes del Director.
2. Desde Windows, mandar un texto administrativo **ficticio** (no un movimiento real): por ejemplo `"Esto es una prueba, no es un movimiento real — ignorar"`.
3. Observar en la Bandeja de tareas de la Mac:
   - [ ] La tarea pasa por `parsing` → `classified` (o `needs_information` si el texto ficticio no tiene datos suficientes, que es lo esperable).
   - [ ] Se puede ver qué proveedor de IA respondió (`extraction_provider` en el detalle del evento).
4. **Cancelar la tarea** (no aprobarla) — el objetivo de este smoke test es confirmar que la extracción/clasificación real funciona de punta a punta, no ejecutar nada.
   - [ ] Confirmar que cancelar no dispara ningún `execute_action()` (no debería haber ninguna fila nueva en `execution_attempts` para esta tarea).

---

## 13. Desinstalación y revocación (al terminar la prueba, o si hace falta resetear)

**Revocar el dispositivo de Marianela** (Mac, Ajustes → dispositivo → Revocar) — invalida el token ya, sin depender de que ella desinstale nada.

**Resetear el rol/pairing en la PC de Marianela** (para volver a probar desde cero):
- Windows: cerrar NicOS Desktop, borrar la carpeta de configuración — típicamente `%APPDATA%\nicos-desktop\nicos-settings.json` (el nombre exacto depende de `app.getPath('userData')`; confirmarlo mirando dónde escribe `electron/settings-store.js` en esa instalación). Al volver a abrir, aparece el selector de rol de nuevo.
- Mac (si se usó para simular Operativa en pruebas): mismo archivo en `~/Library/Application Support/nicos-desktop/nicos-settings.json`.

**Desinstalar del todo:**
- Si se usó la Opción A (código fuente): simplemente borrar la carpeta copiada en la PC de Marianela.
- Si se usó la Opción B (instalador `.exe`): desinstalar desde "Agregar o quitar programas" de Windows, como cualquier app.

**Revocación en Tailscale** (capa adicional, independiente del token de NicOS): desde el admin console de Tailscale (`login.tailscale.com/admin/machines`), se puede desconectar/eliminar la máquina de Marianela de la red de Tailscale — dos revocaciones independientes (token de NicOS + red de Tailscale), documentado también en el README.
