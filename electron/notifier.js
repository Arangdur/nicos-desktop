// Notificaciones nativas de macOS cuando llega algo nuevo a cualquiera de las
// tres bandejas del Director -- pedido real de Nicolás (01/09): quiere
// enterarse en el momento aunque esté en otra app (Chrome/DrApp con un
// paciente adentro), no recién cuando vuelve a abrir NicOS.
//
// Solo corre para el Director (ver guard en main.js) -- es su Mac, habla
// contra su propio sidecar local, y las tres bandejas (WhatsApp, Mail,
// Tareas de Operativa) son justamente lo que él pidió. Sondea cada
// POLL_INTERVAL_MS en vez de que el sidecar empuje eventos -- mismo patrón
// simple que ya usa el resto de la app (polling, no websockets).
const { Notification } = require('electron');

const POLL_INTERVAL_MS = 20000;
let pollInterval = null;

// Un set de ids "ya vistos" y un flag de "primera vuelta" POR FUENTE -- si
// se notificara todo junto recién cuando las TRES fuentes completaron su
// primera consulta, una falla transitoria en una sola bandera dejaría a las
// otras dos avisando de cosas viejas en su siguiente vuelta (falso "nuevo").
const fuentes = {
  whatsapp: { vistos: new Set(), baseline: false },
  mail: { vistos: new Set(), baseline: false },
  tareas: { vistos: new Set(), baseline: false },
};

async function _fetchJson(port, path) {
  const res = await fetch(`http://127.0.0.1:${port}${path}`);
  return res.json();
}

function _notificar(title, body) {
  new Notification({ title, body: body || '' }).show();
}

async function _revisarFuente(nombre, cargar) {
  const f = fuentes[nombre];
  const items = await cargar();
  for (const item of items) {
    if (f.vistos.has(item.id)) continue;
    f.vistos.add(item.id);
    if (f.baseline) _notificar(item.title, item.body);
  }
  f.baseline = true; // recién de la SEGUNDA vuelta en adelante avisa de verdad
}

async function _tick(getPort) {
  const port = getPort();
  if (!port) return;

  await Promise.allSettled([
    _revisarFuente('whatsapp', async () => {
      const data = await _fetchJson(port, '/api/v1/whatsapp/mensajes');
      if (!data.ok) return [];
      return data.mensajes.map((m) => ({
        id: m.id,
        title: 'WhatsApp nuevo',
        body: `${m.telefono}: ${(m.texto_original || '').slice(0, 120)}`,
      }));
    }),
    _revisarFuente('mail', async () => {
      const data = await _fetchJson(port, '/api/v1/mail');
      if (!data.ok) return [];
      return data.mail.map((m) => ({
        id: m.id,
        title: `Mail nuevo (${m.casilla})`,
        body: `${m.remitente}: ${m.asunto || '(sin asunto)'}`,
      }));
    }),
    _revisarFuente('tareas', async () => {
      const data = await _fetchJson(port, '/api/v1/tasks');
      if (!data.ok) return [];
      return data.tasks.map((t) => ({
        id: t.task_id,
        title: 'Tarea nueva cargada',
        body: (t.raw_text || '').slice(0, 120),
      }));
    }),
  ]);
}

function start(getPort) {
  if (pollInterval) return;
  _tick(getPort); // primera pasada inmediata -- arma el baseline sin avisar
  pollInterval = setInterval(() => _tick(getPort), POLL_INTERVAL_MS);
}

function stop() {
  if (pollInterval) { clearInterval(pollInterval); pollInterval = null; }
}

module.exports = { start, stop };
