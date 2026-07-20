# Informe de sesión — 19/20 de julio de 2026 (madrugada)

Pediste trabajar "en automático": auditar todo, pasar del modelo de roles
mockup al modelo de datos real, y dejar listo un instalador de Windows
administrable de forma remota desde tu Mac. Esto es exactamente lo que se
hizo, en qué orden, qué quedó probado de verdad y qué sigue dependiendo de vos.

Nada de esto se activó contra datos reales ni contra una PC física — todo
corrió en bases y perfiles de Electron aislados (`/tmp/...`), igual que el
resto de la sesión.

## Resumen para leer en 30 segundos

1. **Modelo de datos real**: hecho, probado, 26/26 tests en verde. `users` dejó
   de ser una tabla fija de 2 personas y es un catálogo abierto de verdad.
2. **Pantalla de login/alta real en Electron**: hecha, probada en vivo de
   punta a punta (generé un código como Director, completé un alta real como
   "Carlos Fernández — Enfermero", entré con PIN, cambié de persona).
3. **Instalador Windows con auto-actualización**: el código está listo y no
   rompe nada, pero **no está activo** — falta que vos crees el repo real en
   GitHub (ver checklist abajo).
4. **Las pantallas de función del rol Enfermero** (cargar novedades, tildar
   medicación) siguen siendo mockup — no se tocaron esta noche. Lo que cambió
   es que la *cuenta* de un enfermero ya es real, no la pantalla de trabajo.
5. Todo quedó commiteado en `feature/nicos-v0.2` con tags `v0.2.2-rc1/rc2/rc3`
   — nada se subió a `main`, nada se pusheó a ningún remoto (no hay uno
   configurado todavía).

## 1. Auditoría inicial

Antes de tocar código, se revisó el estado real (no lo que decían los
mensajes anteriores):

- `git status` mostró el rediseño visual de la sesión previa sin commitear —
  se commiteó primero, separado de todo lo nuevo, para no mezclar "ya probado
  antes" con "nuevo esta noche".
- Se leyó `sidecar/db.py`, `pairing.py`, `server.py`, `electron/main.js` y
  `settings-store.js` para entender exactamente qué era mockup y qué era
  real. Hallazgo clave: `pairing.complete_pairing()` tenía **hardcodeado**
  `user_id="marianela"` como default — es decir, el sistema real, antes de
  esta noche, literalmente no podía dar de alta a nadie que no fuera ella.
  Eso confirma por qué hacía falta este trabajo, no solo los mockups.

## 2. Modelo de datos real (tags `v0.2.2-rc1`)

**Migración 005** (`sidecar/migrations/005_roles_extensibles.sql`): recrea
`users` (SQLite no permite `ALTER` de un `CHECK`) agregando `dni`,
`fecha_nacimiento`, `sexo`, `turno`, `pin_hash`, `created_by`, `revoked_at`, y
quitando el `CHECK(role IN ('director','operativa'))` — ahora es un catálogo
abierto. `pairing_codes` gana `assigned_role`, `assigned_turno`,
`assigned_display_name`, `created_by`.

**Detalle técnico verificado, no solo asumido**: a diferencia de la
recreación de `execution_attempts` en la migración 003 (que nadie
referenciaba), `users` SÍ es referenciada por FK desde `devices` y `tasks`.
Se probó empíricamente (script descartable, antes de tocar la migración de
verdad) que con `PRAGMA foreign_keys = ON` un `DROP TABLE users` falla con
`FOREIGN KEY constraint failed` — hace falta desactivar el chequeo de FK
solo durante esa recreación puntual y reactivarlo antes de terminar (patrón
oficial de sqlite.org). La migración real usa ese patrón y se validó contra
la cadena completa 001→005.

**Backend** (`sidecar/pairing.py`, `sidecar/pin.py` nuevo):
- `start_pairing(role, turno, created_by, display_name)` — el Director elige
  rol y turno ANTES de generar el código.
- `complete_pairing(...)` — ahora exige nombre, DNI, fecha de nacimiento,
  sexo y PIN, y es quien CREA la fila real en `users` (antes no creaba nada,
  solo emitía un token para un usuario que ya "existía" por hardcodeo).
- `pin.py`: hashing del PIN de 4 dígitos, separado y con su propia
  documentación de por qué NO es el mecanismo de seguridad real (eso sigue
  siendo el token de dispositivo sobre Tailscale).
- `verify_token()` ahora también revisa que la PERSONA no esté revocada, no
  solo el dispositivo puntual.
- `list_users()` / `list_pending_codes()` / `revoke_user()` /
  `cancel_pairing_code()` nuevas, para la pantalla del Director.

**Rutas nuevas** (`server.py`, mismo guard `is_lan()`→403 que ya usan las
rutas admin existentes): `GET /api/v1/users`, `POST /api/v1/users/:id/revoke`,
`POST /api/v1/pairing/:code/cancel`, `POST /api/v1/pin/verify` (esta última
quedó implementada y testeada pero el cliente real de Electron termina
verificando el PIN localmente por la razón explicada en el punto 3 — queda
disponible para un futuro "reseteo remoto de PIN" si hiciera falta).

**Tests**: `sidecar/tests/test_roles_extensibles.py`, 15 casos nuevos —
migración+integridad referencial, el código lleva el rol que elegiste, el
alta crea una ficha completa, el PIN nunca queda en texto plano ni se expone
en `list_users()`, revocar a una persona bloquea su próximo login de
inmediato, el `CHECK` de sexo funciona, insertar rol `'enfermero'` funciona
sin tocar el `CHECK` viejo, nombres repetidos no colisionan de `user_id`,
los códigos pendientes aparecen y desaparecen bien. **Suite completo:
26/26 archivos, sin regresiones** (corrido dos veces, antes y después del
bloque de Electron).

## 3. Electron real (tag `v0.2.2-rc2`)

- `settings-store.js`: reemplaza el único `PAIRED_DEVICE_TOKEN` por
  `IDENTITIES_JSON` (array cifrado) — una misma PC ahora puede tener varias
  personas vinculadas (Carlos y María compartiendo la PC de enfermería, cada
  una con su propio token).
- `operativa-client.js`: `completeAlta()` / `loginWithPin()` nuevas. **El
  login por PIN es intencionalmente 100% local, sin red** — si dependiera de
  consultar a la Mac, alguien legítimamente vinculado no podría ni abrir la
  app cuando tu Mac está apagada o dormida, rompiendo el diseño que ya
  existía de "cola local si no hay conexión" para el envío de tareas. El PIN
  nunca fue el mecanismo de seguridad real; eso es el token, emitido en el
  alta contra el servidor real.
- `renderer/shared/login.html` + `login.js` (nuevo) reemplaza
  `pairing.html` — "¿Quién sos?" (elegir identidad guardada + PIN) / "Soy
  nuevo" (alta completa). Reusa la IP ya guardada si otra persona ya vinculó
  esa PC, para no pedirla de nuevo.
- `renderer/enfermero/` (nuevo): pantalla real, identidad ya funciona, pero
  dice explícitamente "todavía en construcción" para la carga de novedades en
  vez de mostrar datos falsos.
- `settings-panel-director.js`: "Vincular nuevo dispositivo" pasó a
  "Personas con acceso a NicOS" — ahora pide nombre + rol + turno antes de
  generar el código, y la lista combina personas ya vinculadas con códigos
  pendientes (leyendo `GET /api/v1/users`).
- `settings-panel-operativa.js`: "Olvidar vinculación" (todo el dispositivo)
  pasó a "Cambiar de persona" / "Olvidar mi acceso" (solo la persona activa,
  sin afectar a otras identidades en la misma PC).

**Verificado en vivo, no solo leído**: con un Director y una Operativa/
Enfermero aislados corriendo de verdad —
1. Generé un código como Director con rol "Enfermero/a de Abate".
2. Completé el alta real como "Carlos Fernández" con DNI, fecha de
   nacimiento, sexo y PIN — un código vencido (5 min) mostró el error real
   del backend ("Este código venció..."), no uno simulado, confirmando que
   IPC→HTTP→sidecar está conectado de punta a punta.
3. Caí directo en la pantalla real de Enfermería, con "Carlos Fernández" en
   el encabezado.
4. Ajustes mostró su identidad correctamente.
5. "Cambiar de persona" volvió al login; elegir "Carlos Fernández" + PIN
   correcto entró sin volver a tocar la red (verificación local, como se
   diseñó).

## 4. Instalador Windows + auto-actualización (tag `v0.2.2-rc3`)

**Decisión de diseño explícita, no lo que pediste literalmente**: en vez de
un canal de comando remoto a medida (vos "empujando" algo en vivo a la PC de
Marianela o de Abate — que mal diseñado es un mecanismo de ejecución remota),
usé el patrón estándar de Electron: `electron-updater` apuntando a GitHub
Releases del mismo repo. El resultado práctico es el mismo que pediste — no
tener que ir físicamente a instalar una actualización — pero sin inventar un
mecanismo de seguridad nuevo y sin auditar. Te lo marco acá explícitamente
para que lo puedas objetar si preferís otra cosa.

Flujo real una vez activado: corrés `git tag v0.2.3 && git push --tags`
desde tu Mac → el workflow de GitHub Actions (ya existente, ahora ampliado)
compila el `.exe` y lo publica como Release → cada PC no-Director lo detecta
la próxima vez que lo busca (automático cada 4 horas, o a demanda con el
botón "Buscar actualizaciones ahora" en Ajustes).

- `electron/auto-updater.js` (nuevo): `autoDownload = false` a propósito —
  nada se instala sin que la persona lo confirme.
- Activo **solo** para instalaciones no-Director y **solo** en la versión
  empaquetada (nunca en tu Mac, nunca en modo desarrollo).
- Tarjeta "Actualizaciones" nueva en Ajustes de Operativa/Enfermero: versión
  instalada, botón de búsqueda manual, progreso de descarga, confirmación
  antes de reiniciar para instalar.
- `package.json`: `build.win.publish` con provider `github` y placeholders
  `TU_USUARIO_DE_GITHUB` / `TU_REPO_DE_GITHUB` — **no funciona hasta que los
  completes** (ver checklist).
- `.github/workflows/build-windows.yml`: ahora también se dispara con tags
  `v*`, y en ese caso publica el instalador como Release (antes solo lo
  subía como artefacto interno de Actions, no descargable por
  `electron-updater`).

**Verificado en vivo**: la tarjeta de Actualizaciones renderiza la versión
instalada correctamente (`0.2.2-rc.1`) y el botón no rompe la app en modo
desarrollo (falla limpio, como corresponde sin un feed de releases real).

## Qué NO se tocó esta noche (para que no asumas que sí)

- Las pantallas de función del rol Enfermero (cargar novedades, tildar
  medicación administrada) — siguen siendo mockup (`mockups/enfermeria-abate.html`).
- El módulo clínico de consultorio — sigue en mockup, sigue esperando la
  revisión legal del §8 del `NICOS_MASTER_SCOPE.md`.
- Nada de Finanzas/Trading/Jefe de Gabinete — siguen en mockup.
- No se cortó ningún release real, no se pusheó nada a ningún remoto (no
  hay uno configurado), no se instaló nada en una PC Windows física.

## Checklist para vos — en orden, antes de seguir

1. **Repasar el diseño de auto-actualización del punto 4** — si preferís un
   mecanismo distinto al de GitHub Releases, decilo antes de activarlo.
2. **Crear el repositorio en GitHub** (privado, dado que este es código con
   lógica de negocio real de Abate/consultorio) y conectar el remoto local:
   `git remote add origin <url> && git push -u origin feature/nicos-v0.2`.
3. **Completar `package.json` → `build.win.publish.owner` / `.repo`** con los
   datos reales de ese repositorio.
4. Recién ahí, **cortar el primer tag real** (ej. `git tag v0.2.2 && git push
   --tags`) para probar el ciclo completo de auto-actualización — la primera
   vez conviene hacerlo con una instalación de prueba, no con la PC real de
   Marianela.
5. **Prueba física real**: instalar en una PC Windows de verdad (o una VM),
   pairing real por Tailscale (no loopback), y ahí sí la llamada real a
   Claude/OpenAI con una API key de verdad — nada de esto se hizo esta noche
   a propósito, según lo que ya habíamos acordado (`NICOS_MASTER_SCOPE.md` §9,
   punto 11).
6. Cuando quieras seguir con el roadmap: el siguiente paso natural documentado
   en `NICOS_MASTER_SCOPE.md` §9 es el módulo clínico (punto 3), que sigue
   esperando la revisión legal del §8 antes de tocar un dato real de paciente.

## Commits y tags de esta sesión

Todo en la rama `feature/nicos-v0.2`, nada en `main`:

```
cea3c9e  Rediseño visual del sistema de UI (Director + Operativa)
ea13814  Documento maestro de alcance + mockups exploratorios de todos los módulos
39904a3  v0.2.2-rc1: modelo de datos real para roles extensibles         [tag v0.2.2-rc1]
f87b549  v0.2.2-rc2: Electron real -- login/alta multi-identidad         [tag v0.2.2-rc2]
188f7a7  v0.2.2-rc.1: instalador Windows con auto-actualización remota   [tag v0.2.2-rc3]
```

(El último commit se llama a sí mismo "rc.1" en el texto por un desliz de
numeración al escribirlo, pero el tag real que le corresponde en la
secuencia de esta noche es `v0.2.2-rc3` — dejo la aclaración acá para que no
confunda al leerlo después.)
