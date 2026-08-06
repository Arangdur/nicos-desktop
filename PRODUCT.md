# Product

<!-- impeccable:product-schema 1 -->

## Platform

web

## Users

Tres roles, cada uno con su propia vista, nunca una vista genérica compartida:

- **Director** (Nicolás Buso, psiquiatra y Director Técnico de la Fundación Abate) — usa su Mac.
  Coordina el consultorio, las finanzas personales y de la Fundación, y Abate. Es el único que
  ve claves de IA/Google, aprueba tareas sensibles, y tiene acceso administrativo completo.
- **Operativa** (Marianela, secretaria del consultorio, sin perfil técnico) — usa una PC Windows
  aparte. Carga turnos/mensajes, ve recordatorios y la bandeja de WhatsApp, nunca ve secretos ni
  nada clínico. Su feedback directo sobre esta vista: "muy básica, poco friendly, sin
  visibilización de WhatsApp, sin calendario" — ya se sumaron ambas cosas, pero el tono general
  de la interfaz sigue pendiente de mejorar.
- **Enfermero/a de Abate** (personal de enfermería de la Fundación) — carga novedades y tilda
  medicación administrada en tiempo real, en la PC del hogar. Nunca ve nada del consultorio ni
  de las finanzas.

## Product Purpose

Centraliza en un solo lugar lo que antes estaba disperso entre Google Sheets, un workflow de
n8n, mails diarios de DrApp, y anotaciones en papel: turnos, mensajes de WhatsApp de pacientes,
tareas administrativas y financieras del consultorio/Fundación, y el registro de cuidado de
Abate. El éxito es que cada persona (Nicolás, Marianela, el equipo de enfermería) resuelva su
trabajo diario desde una sola app, sin tener que saltar entre herramientas ni depender de que
alguien más esté disponible para cargar un dato.

## Positioning

A diferencia de usar DrApp + WhatsApp + Sheets + n8n por separado, NicOS es la única pieza que:
(a) clasifica y redacta con IA pero **nunca ejecuta nada sensible sin aprobación humana
explícita** (ni un pago, ni una respuesta a un paciente), y (b) separa estrictamente qué ve cada
rol — Marianela nunca ve una clave ni un dato clínico, el enfermero nunca ve finanzas.

## Operating Context

- App de escritorio real (Electron + sidecar Python/SQLite), no un dashboard web público — cada
  persona la abre desde su propia PC/Mac.
- Red: nunca expuesta a internet salvo una única ruta puntual (WhatsApp entrante, protegida por
  verificación de firma, vía Tailscale Funnel) — todo lo demás vive solo en la red privada de
  Tailscale entre las máquinas de Nicolás, Marianela y el equipo de Abate.
- El flujo central es "algo entra (mensaje, pedido, turno) → IA lo clasifica/redacta → una
  persona con el rol correcto aprueba o rechaza → recién ahí se ejecuta" — se repite en tareas
  financieras, mensajes de WhatsApp, y en menor medida en Abate.
- Uso diario real, no un prototipo: hay pacientes, plata y turnos reales pasando por acá.

## Capabilities and Constraints

- Gestión de turnos de Medicina General (recordatorios automáticos por WhatsApp, calendario
  mensual) — Psiquiatría queda afuera a propósito, ya tiene su propio recordatorio nativo en
  DrApp.
- Bandeja de WhatsApp entrante: un paciente escribe, la IA arma un borrador de respuesta, una
  persona lo aprueba o edita antes de que salga. Nunca hay un envío automático sin ese paso.
- Bandeja de tareas administrativas/financieras con aprobación humana obligatoria antes de
  ejecutar cualquier movimiento.
- Módulo Abate: alta de residentes, tratamiento vigente, medicación con horarios en vivo,
  novedades de enfermería.
- Restricción dura por rol, aplicada en el servidor (no solo ocultando botones en la interfaz):
  Operativa nunca puede aprobar algo marcado como clínico; Enfermero nunca ve nada fuera de
  Abate; solo el Director ve claves de IA/Google/Twilio/DrApp.
- Nada de historia clínica ni recetas todavía — pendiente de revisión legal (Ley de Salud
  Mental, derechos del paciente, protección de datos) antes de sumar esos permisos.

## Brand Commitments

Sin identidad de marca externa — es una herramienta interna, no un producto que se vende. El
nombre "NicOS Desktop" y el estilo visual actual (paleta navy/azul, ver
`renderer/shared/styles.css`) son la única identidad establecida hasta ahora; no hay logo, ni
guía de marca formal.

## Evidence on Hand

Sistema de diseño real ya en uso, no hipotético: `renderer/shared/styles.css` define variables
(`--navy`, `--space-*`, `--text-*`, `--border`, tags de estado como `.tag.proceso`/`.tag.nuevo`)
usadas de forma consistente en las pantallas de Director y Operativa. Screenshots reales
disponibles vía Playwright (`_electron`) contra la app corriendo — no hace falta inventar
mockups, se puede ver y capturar la interfaz real en cualquier momento.

## Product Principles

1. La aprobación humana nunca es opcional para algo sensible (plata, pacientes, mensajes
   salientes) — la IA propone, nunca decide ni ejecuta sola.
2. Cada rol ve exactamente lo que necesita para su trabajo y nada más — la restricción es del
   servidor, no cosmética.
3. Nunca se expone más superficie de red de la estrictamente necesaria — cada excepción (como
   el webhook de WhatsApp) es una decisión explícita y puntual, nunca un default.
4. La interfaz tiene que funcionar para gente sin perfil técnico (Marianela, el equipo de
   enfermería) tanto como para el Director — "básico y poco friendly" no es aceptable para
   quien usa la app todos los días para su trabajo real.
5. Nunca se inventa un dato que la app no tiene de verdad (un horario disponible, un teléfono,
   una respuesta clínica) — mejor mostrar que falta información que fabricarla.

## Accessibility & Inclusion

Sin requisito de accesibilidad formal establecido todavía (no hay usuarios con necesidades
específicas confirmadas), pero el principio de "tiene que ser fácil de usar para gente sin
perfil técnico" aplica en la práctica como una barra alta de claridad, tipografía legible, y
bajo esfuerzo cognitivo — tratarlo con el mismo estándar que si fuera un requisito formal.
