# Instalar NicOS Desktop en la PC de Marianela (Windows)

Guía paso a paso, sin dar nada por sabido. Se hace una sola vez — después,
Marianela abre la app y entra con su nombre y su PIN, nada más.

Necesitás dos cosas de tu lado (Mac) antes de empezar:
- Tener NicOS Desktop abierto, con tu sesión de Director.
- Saber tu IP de Tailscale (Paso 2 te dice cómo verla).

Tiempo total: 15-20 minutos la primera vez.

---

## Paso 1 — Instalar Tailscale en la PC de Marianela

Tailscale es la "red privada" que hace que la PC de Marianela y tu Mac se
hablen de forma segura, aunque no estén en la misma red WiFi. Si ya lo
instalaron en una prueba anterior, abrí el ícono de Tailscale (bandeja del
sistema, abajo a la derecha) y confirmá que dice **"Connected"** — si es
así, salteá directo al Paso 2.

Si no está instalado:

1. En la PC de Marianela, abrir el navegador e ir a:
   `https://tailscale.com/download/windows`
2. Descargar e instalar (siguiente, siguiente, como cualquier programa).
3. Al abrirse, va a pedir iniciar sesión. Usá **la misma cuenta de
   Tailscale que usás vos** — se la compartís vos (invitación o
   credenciales), no se crea una cuenta nueva para ella.
4. Confirmar en el ícono de la bandeja del sistema que dice "Connected".

---

## Paso 2 — Anotar tu IP de Tailscale (en tu Mac)

En tu Mac, abrí la Terminal y escribí:

```bash
tailscale ip -4
```

Te va a devolver algo como `100.87.23.14` (siempre empieza con `100.`).
**Anotala en un papel o en las Notas del celular** — la vas a necesitar en
el Paso 4, y Marianela también la va a necesitar si en algún momento hay
que volver a vincular su PC.

---

## Paso 3 — Descargar e instalar NicOS Desktop en la PC de Marianela

1. En la PC de Marianela, abrir el navegador e ir a:
   `https://github.com/Arangdur/nicos-desktop/releases/latest`
2. Buscar el archivo que termina en `.exe` (algo como
   `NicOS-Desktop-0.2.4-x64.exe`) y hacer clic para descargarlo.
3. Abrir el archivo descargado (normalmente queda en la carpeta
   "Descargas"). Windows va a mostrar una pantalla azul que dice algo
   como **"Windows protegió su PC"** — esto es normal, pasa porque el
   instalador todavía no está firmado digitalmente (no es un virus).
   Hacer clic en **"Más información"**, y después en el botón que aparece,
   **"Ejecutar de todas formas"**.
4. Seguir el instalador (siguiente, siguiente, instalar). Al terminar, se
   abre solo la app.

---

## Paso 4 — Dar de alta a Marianela (una sola vez)

Esta parte tiene dos mitades: una la hacés vos en tu Mac, la otra la hace
Marianela en su PC, casi al mismo tiempo.

### De tu lado (Mac), primero:

1. Abrí NicOS Desktop → pestaña **Ajustes**.
2. Bajá hasta la tarjeta **"Personas con acceso a NicOS"**.
3. Completá:
   - **Nombre completo**: el de Marianela.
   - **Rol**: elegí **"Operativa (Secretaria)"**.
4. Hacé clic en **"Generar código de vinculación"**.
5. Va a aparecer un código de 6 dígitos grande en pantalla, con el
   mensaje "válido 5 minutos". **Este código dura solo 5 minutos** — si se
   vence antes de que Marianela termine el paso siguiente, simplemente
   volvés a hacer clic en el botón y genera uno nuevo.

### Del lado de Marianela (su PC), con la app recién instalada abierta:

Como es la primera vez que se abre esta app en esta PC, va a mostrar
directamente una pantalla para completar sus datos (no un menú de
personas, porque todavía no hay nadie cargado ahí).

1. **Nombre completo**: el mismo nombre que pusiste vos en el paso
   anterior (no hace falta que coincida letra por letra, pero mejor si
   coincide).
2. **DNI**.
3. **Fecha de nacimiento**.
4. **Sexo**.
5. **PIN**: 4 números que Marianela va a usar de ahora en más para entrar
   a la app (como el PIN de una tarjeta). Que lo elija ella y lo recuerde
   — no hace falta anotarlo en ningún lado, es solo para esta app.
6. **Código**: los 6 dígitos que generaste vos en el paso anterior.
7. **IP**: tu IP de Tailscale del Paso 2 (`100.x.y.z`). Si este campo no
   aparece, es porque esta PC ya tenía guardada la IP de una vinculación
   anterior — está bien, no hace falta tocarlo.
8. Hacer clic en el botón para completar el alta.

Si todo salió bien, la pantalla cambia sola a la vista de Marianela
(Operativa) — no hace falta hacer nada más. Si da error, lo más común es
que el código ya venció (generá uno nuevo en tu Mac) o que la IP esté mal
escrita.

**Confirmación de tu lado**: en tu Mac, Ajustes → "Personas con acceso a
NicOS", el nombre de Marianela debería pasar de "Código generado" a
aparecer como persona activa.

---

## Paso 5 — Uso día a día (después de esta primera vez)

De ahora en más, cuando Marianela abra NicOS Desktop:

1. Ve su nombre en una lista (puede haber más de una persona si en algún
   momento se suma alguien más).
2. Hace clic en su nombre.
3. Escribe su PIN de 4 dígitos.
4. Entra directo a su pantalla de trabajo.

No tiene que volver a poner el código ni la IP nunca más — eso fue solo
para esta primera vez.

---

## Actualizaciones: no hay que reinstalar nada

NicOS Desktop se actualiza solo. Cuando publiques una versión nueva, la
próxima vez que Marianela abra la app (o la tenga abierta), la va a
detectar y actualizar en segundo plano — no hace falta volver a descargar
ni instalar el `.exe` de nuevo, ni repetir ningún paso de esta guía.

---

## Si algo no funciona

- **"No se pudo conectar con la Mac"** al completar el alta: lo más
  probable es que tu Mac no tenga Tailscale activo en ese momento, o que
  NicOS Desktop no esté abierto en tu Mac. Confirmá `tailscale status` en
  tu Mac (tiene que decir conectado, no "Stopped") y que la app esté
  abierta con tu sesión de Director.
- **El código de 6 dígitos "vencido" o "inválido"**: duran 5 minutos.
  Simplemente generá uno nuevo desde tu Mac y probá de nuevo.
- **La pantalla azul de Windows no deja seguir**: si no aparece la opción
  "Más información", hacer clic en "No usar esta app" y volver a intentar
  — a veces Windows la muestra distinto según la versión. Si persiste,
  avisame y lo vemos juntos.
- **Se cerró la app y no sabés si quedó bien instalada**: volver a
  abrirla desde el ícono que quedó en el Escritorio o en el menú Inicio —
  no hace falta reinstalar.
