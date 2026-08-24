// Vista Director — reusa el mismo estilo de loaders que jarvis-trabajo/dashboard.html
// (fetch a un servidor local, fmtNum/escHtml, badges de estado) para que evolucionar
// este código se sienta igual que evolucionar el dashboard existente.

const params = new URLSearchParams(window.location.search);
let API = null; // se arma una vez sabemos el puerto real del sidecar

let chatHistory = [];
let currentBrain = 'claude';
let cfoChart = null; // instancia de Chart.js del gráfico de barras del Resumen -- se destruye y recrea en cada loadResumen()

function fmtNum(n) {
  if (n === null || n === undefined || Number.isNaN(n)) return '—';
  return Number(n).toLocaleString('es-AR', { maximumFractionDigits: 2 });
}

function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

async function fetchJson(path, opts) {
  const res = await fetch(`${API}${path}`, opts);
  return res.json();
}

// v0.2.5 -- para las tarjetas de Resumen que vienen de un JSON estático
// (trading_bot, consultorio, cfo_vivo) -- sin esto, un dato de hace 3
// semanas se veía exactamente igual de "fresco" que uno de hoy. Puramente
// informativo, sin semáforo de alarma: no sabemos cuál es la cadencia
// esperada de cada fuente como para decidir qué es "viejo".
function tiempoRelativo(fechaISO) {
  if (!fechaISO) return null;
  const fecha = new Date(fechaISO);
  if (Number.isNaN(fecha.getTime())) return null;
  const diffMs = Date.now() - fecha.getTime();
  const dias = Math.floor(diffMs / (1000 * 60 * 60 * 24));
  if (dias <= 0) return 'actualizado hoy';
  if (dias === 1) return 'actualizado ayer';
  if (dias < 30) return `actualizado hace ${dias} días`;
  const meses = Math.floor(dias / 30);
  return `actualizado hace ${meses} mes${meses === 1 ? '' : 'es'}`;
}

function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach((el) => (el.style.display = 'none'));
  document.getElementById(`tab-${name}`).style.display = 'block';
  document.querySelectorAll('[data-tab]').forEach((btn) => {
    btn.classList.toggle('active', btn.dataset.tab === name);
  });
}

document.querySelectorAll('[data-tab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    switchTab(btn.dataset.tab);
    if (btn.dataset.tab === 'tareas') renderTasksList();
    if (btn.dataset.tab === 'abate') { loadAbateTab(); _marcarAbateVisto(); }
    if (btn.dataset.tab === 'recordatorios') loadRecordatoriosTab();
    if (btn.dataset.tab === 'mensajes-whatsapp') loadMensajesWhatsappTab();
    if (btn.dataset.tab === 'facturas') loadFacturasTab();
    if (btn.dataset.tab === 'mail') loadMailTab();
    if (btn.dataset.tab === 'ajustes') loadAjustes();
    if (btn.dataset.tab === 'acerca-de') loadAcercaDe();
  });
});

// v0.2.5 -- "novedades sin leer" de Abate: no existe un concepto de
// leído/no-leído en la base (abate_novedades no tiene esa columna, y
// agregarla es una migración que no hace falta para esto). En cambio, se
// guarda acá mismo (en esta Mac, donde vive el Director) la última vez que
// se abrió la pestaña Abate -- "sin leer" pasa a significar honestamente
// "cargada después de la última vez que entraste a mirar", que es lo que
// de verdad le importa a Nicolás en el Resumen.
const ABATE_VISITA_KEY = 'nicos_abate_ultima_visita';
function _marcarAbateVisto() {
  try { localStorage.setItem(ABATE_VISITA_KEY, new Date().toISOString()); } catch (e) { /* no bloquea nada si falla */ }
}
function _abateUltimaVisita() {
  try { return localStorage.getItem(ABATE_VISITA_KEY) || '1970-01-01T00:00:00'; } catch (e) { return '1970-01-01T00:00:00'; }
}

// Mismo cálculo de "atrasada" que ya usa renderer/enfermero/app.js
// (_horarioVencido) -- se repite acá porque son dos vistas/archivos
// distintos, no porque la lógica cambie.
function _horarioVencidoDir(horario) {
  const [h, m] = horario.split(':').map(Number);
  const ahora = new Date();
  return (h * 60 + m) < (ahora.getHours() * 60 + ahora.getMinutes());
}

async function _cargarConteosAbate() {
  const residentesData = await fetchJson('/api/v1/abate/residentes').catch(() => null);
  const residentes = residentesData && residentesData.ok ? residentesData.residentes : [];

  const [tratamientos, administraciones, novedadesData] = await Promise.all([
    Promise.all(residentes.map((r) => fetchJson(`/api/v1/abate/residentes/${r.residente_id}/tratamiento`).catch(() => null))),
    Promise.all(residentes.map((r) => fetchJson(`/api/v1/abate/residentes/${r.residente_id}/administraciones`).catch(() => null))),
    fetchJson('/api/v1/abate/novedades').catch(() => null),
  ]);

  let medicacionAtrasada = 0;
  residentes.forEach((r, i) => {
    const trat = tratamientos[i] && tratamientos[i].ok ? tratamientos[i].tratamiento : null;
    if (!trat) return;
    const admins = administraciones[i] && administraciones[i].ok ? administraciones[i].administraciones : [];
    const yaAdministradas = new Set(admins.map((a) => `${a.droga}|${a.horario_previsto}`));
    (trat.medicacion || []).forEach((m) => {
      (m.horarios || []).forEach((h) => {
        if (!yaAdministradas.has(`${m.droga}|${h}`) && _horarioVencidoDir(h)) medicacionAtrasada++;
      });
    });
  });

  const ultimaVisita = _abateUltimaVisita();
  const novedadesNuevas = novedadesData && novedadesData.ok
    ? novedadesData.novedades.filter((n) => n.created_at > ultimaVisita).length
    : 0;

  return { medicacionAtrasada, novedadesNuevas };
}

function loadAcercaDe() {
  renderAboutPanel(document.getElementById('tab-acerca-de'));
}

// v0.2.5 -- "mejorar el dashboard": antes el Resumen era solo una foto
// estática de archivos JSON externos (trading bot, consultorio, cowork) --
// no decía nada de lo que está pasando DENTRO de NicOS ahora mismo (tareas
// por aprobar, WhatsApp esperando, turnos con problema). Se agrega esta
// sección arriba de todo, con contadores en vivo de las mismas rutas que ya
// usan sus pestañas -- no hay lógica nueva del lado del servidor, solo se
// reusa y se resume acá.
async function _cargarConteosAtencion() {
  const [tareas, mensajes, recordatorios, abate, facturas, mail] = await Promise.all([
    fetchJson('/api/v1/tasks?state=pending_approval').catch(() => null),
    fetchJson('/api/v1/whatsapp/mensajes').catch(() => null),
    fetchJson('/api/v1/recordatorios').catch(() => null),
    _cargarConteosAbate(),
    fetchJson('/api/v1/facturas').catch(() => null),
    fetchJson('/api/v1/mail').catch(() => null),
  ]);
  return {
    tareas: tareas && tareas.ok ? tareas.tasks.length : 0,
    whatsapp: mensajes && mensajes.ok
      ? mensajes.mensajes.filter((m) => m.estado === 'borrador_generado' || m.estado === 'error_clasificacion').length
      : 0,
    turnos: recordatorios && recordatorios.ok
      ? recordatorios.recordatorios.filter((r) => r.estado === 'sin_telefono' || r.estado === 'fallo_envio').length
      : 0,
    medicacionAtrasada: abate.medicacionAtrasada,
    novedadesNuevas: abate.novedadesNuevas,
    facturas: facturas && facturas.ok
      ? facturas.facturas.filter((f) => f.estado === 'borrador_generado' || f.estado === 'error_extraccion').length
      : 0,
    // spam no necesita atención del Director -- ver mail-tab.js.
    mail: mail && mail.ok
      ? mail.mail.filter((m) => (m.estado === 'borrador_generado' || m.estado === 'error_clasificacion') && m.categoria !== 'spam').length
      : 0,
  };
}

function _renderAtencionHtml(counts) {
  const items = [
    { n: counts.tareas, label: counts.tareas === 1 ? 'tarea esperando tu aprobación' : 'tareas esperando tu aprobación', tab: 'tareas' },
    { n: counts.whatsapp, label: counts.whatsapp === 1 ? 'mensaje de WhatsApp con borrador esperando' : 'mensajes de WhatsApp con borrador esperando', tab: 'mensajes-whatsapp' },
    { n: counts.facturas, label: counts.facturas === 1 ? 'pedido de factura esperando aprobación' : 'pedidos de factura esperando aprobación', tab: 'facturas' },
    { n: counts.mail || 0, label: counts.mail === 1 ? 'mail con borrador esperando' : 'mails con borrador esperando', tab: 'mail' },
    { n: counts.turnos, label: counts.turnos === 1 ? 'turno necesita atención (sin teléfono o falló el envío)' : 'turnos necesitan atención (sin teléfono o falló el envío)', tab: 'recordatorios' },
    { n: counts.medicacionAtrasada || 0, label: counts.medicacionAtrasada === 1 ? 'toma de Abate atrasada sin confirmar' : 'tomas de Abate atrasadas sin confirmar', tab: 'abate' },
    { n: counts.novedadesNuevas || 0, label: counts.novedadesNuevas === 1 ? 'novedad nueva de Abate' : 'novedades nuevas de Abate', tab: 'abate' },
  ].filter((i) => i.n > 0);

  if (items.length === 0) {
    return `
      <div class="card" id="card-atencion">
        <h3>Necesita tu atención</h3>
        <div class="empty">Todo al día -- nada pendiente de tu parte.</div>
      </div>
    `;
  }
  return `
    <div class="card" id="card-atencion">
      <h3>Necesita tu atención</h3>
      ${items.map((i) => `
        <div class="row-between atencion-item" data-tab="${i.tab}" style="padding:10px 0; border-bottom:1px solid var(--border); cursor:pointer;">
          <span>${i.label}</span>
          <span class="tag proceso">${i.n}</span>
        </div>
      `).join('')}
    </div>
  `;
}

async function _refrescarAtencion() {
  const el = document.getElementById('card-atencion');
  if (!el) return; // el Resumen ya no está montado (cambió de pestaña antes de que responda)
  const counts = await _cargarConteosAtencion();
  el.outerHTML = _renderAtencionHtml(counts);
  _wireAtencionClicks();
}

function _wireAtencionClicks() {
  document.querySelectorAll('.atencion-item').forEach((item) => {
    item.addEventListener('click', () => {
      const tab = item.dataset.tab;
      document.querySelector(`[data-tab="${tab}"]`)?.click();
    });
  });
}

// v0.2.7 (20/08) -- pedido real de Nicolás: panel de salud de Fase C.
// Antes, ver el estado de una conversación de turno (activa, o trabada en
// 'derivado'/'expirado') significaba una query manual a SQLite -- esto lo
// pone en el Resumen. Solo Director (la ruta es Director-only, ver
// server.py -- es información técnica, no algo para que Marianela resuelva).
const ESPECIALIDAD_LABEL_RESUMEN = { medicina_general: 'Medicina General', psiquiatria: 'Psiquiatría' };
const ESTADO_CONVERSACION_LABEL = {
  esperando_especialidad: 'Preguntando especialidad',
  esperando_eleccion: 'Esperando que elija horario',
  esperando_identificacion: 'Pidiendo DNI/nombre',
  confirmado: 'Turno confirmado',
  cancelado: 'Cancelado',
  expirado: 'Expirado sin resolver',
  derivado: 'Derivado a una persona',
};

function _renderFaseCHtml(conversaciones) {
  const activas = conversaciones.filter((c) => ['esperando_especialidad', 'esperando_eleccion', 'esperando_identificacion'].includes(c.estado));
  const necesitanRevision = conversaciones.filter((c) => c.estado === 'derivado' || c.estado === 'expirado');

  const fila = (c) => `
    <tr>
      <td>${escHtml(c.telefono)}</td>
      <td>${escHtml(ESPECIALIDAD_LABEL_RESUMEN[c.especialidad] || '—')}</td>
      <td>${escHtml(ESTADO_CONVERSACION_LABEL[c.estado] || c.estado)}</td>
      <td>${tiempoRelativo(c.actualizado_at) || '—'}</td>
    </tr>
  `;

  return `
    <div class="card" id="card-fase-c">
      <h3>Turnos por WhatsApp (Fase C)</h3>
      ${activas.length === 0 ? '<div class="empty">Ninguna conversación activa ahora.</div>' : `
        <table>
          <tr><th>Teléfono</th><th>Especialidad</th><th>Estado</th><th>Hace</th></tr>
          ${activas.map(fila).join('')}
        </table>
      `}
      ${necesitanRevision.length > 0 ? `
        <h4 style="margin-top:var(--space-4); text-transform:uppercase; font-size:12px; color:var(--muted); letter-spacing:0.03em;">
          Derivadas o expiradas en las últimas 24hs (${necesitanRevision.length})
        </h4>
        <table>
          <tr><th>Teléfono</th><th>Especialidad</th><th>Estado</th><th>Hace</th></tr>
          ${necesitanRevision.map(fila).join('')}
        </table>
      ` : ''}
    </div>
  `;
}

async function _refrescarFaseC() {
  const el = document.getElementById('card-fase-c');
  if (!el) return; // el Resumen ya no está montado
  const data = await fetchJson('/api/v1/turnos/conversaciones?horas=24').catch(() => null);
  if (!data || !data.ok) return; // no bloquea el resto del Resumen si esto falla
  el.outerHTML = _renderFaseCHtml(data.conversaciones);
}

// v0.2.7 (20/08) -- pedido real de Nicolás: "lo que salió solo hoy" -- los
// tres casos que se auto-envían sin aprobación (saludo puro, acuse de
// receta, seguimiento de receta) quedan marcados uno por uno en la Bandeja,
// pero no había ningún resumen para un chequeo rápido a la mañana.
function _tipoAutoEnvio(m) {
  if (m.clasificacion === 'receta') return 'Receta';
  return 'Saludo';
}

function _renderAutoEnviadosHtml(mensajesHoy) {
  if (mensajesHoy.length === 0) {
    return `
      <div class="card" id="card-auto-enviados">
        <h3>Lo que salió solo hoy</h3>
        <div class="empty">Nada se mandó automático todavía hoy.</div>
      </div>
    `;
  }
  const recetas = mensajesHoy.filter((m) => m.clasificacion === 'receta').length;
  const saludos = mensajesHoy.length - recetas;
  const MOSTRAR = 8;
  return `
    <div class="card" id="card-auto-enviados">
      <h3>Lo que salió solo hoy</h3>
      <div class="metric-grid">
        <div class="metric"><div class="metric-label">Total</div><div class="metric-value">${mensajesHoy.length}</div></div>
        <div class="metric"><div class="metric-label">Saludos</div><div class="metric-value">${saludos}</div></div>
        <div class="metric"><div class="metric-label">Recetas</div><div class="metric-value">${recetas}</div></div>
      </div>
      <table style="margin-top:var(--space-3);">
        <tr><th>Hora</th><th>Teléfono</th><th>Tipo</th></tr>
        ${mensajesHoy.slice(0, MOSTRAR).map((m) => `
          <tr>
            <td>${new Date(m.resuelto_at + 'Z').toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit' })}</td>
            <td>${escHtml(m.telefono)}</td>
            <td>${_tipoAutoEnvio(m)}</td>
          </tr>
        `).join('')}
      </table>
      ${mensajesHoy.length > MOSTRAR ? `<p class="help-text">+ ${mensajesHoy.length - MOSTRAR} más -- ver Bandeja de WhatsApp.</p>` : ''}
    </div>
  `;
}

async function _refrescarAutoEnviados() {
  const el = document.getElementById('card-auto-enviados');
  if (!el) return;
  const data = await fetchJson('/api/v1/whatsapp/mensajes').catch(() => null);
  if (!data || !data.ok) return;
  const hoyISO = new Date().toISOString().slice(0, 10);
  const mensajesHoy = data.mensajes
    .filter((m) => m.resuelto_by === 'sistema' && (m.resuelto_at || '').slice(0, 10) === hoyISO)
    .sort((a, b) => (a.resuelto_at < b.resuelto_at ? 1 : -1));
  el.outerHTML = _renderAutoEnviadosHtml(mensajesHoy);
}

// v0.2.8 (24/08) -- pedido real de Nicolás: "pulso del worker" -- saber sin
// abrir logs si el loop de fondo (sidecar/worker.py) sigue vivo. Mismo
// patrón que _renderFaseCHtml/_refrescarFaseC. Sin `ultimo_tick_ok` reciente
// el worker probablemente murió silenciosamente.
function _renderWorkerHtml(estado) {
  const tick = tiempoRelativo(estado.ultimo_tick_ok);
  const vivo = !!tick; // si nunca tuvo una vuelta ok, no hay forma de saber si está vivo
  return `
    <div class="card" id="card-worker">
      <div class="row-between"><h3 style="margin:0;">Worker de fondo</h3>${estado.iniciado_en ? freshTagGlobal(estado.iniciado_en, 'arrancó') : ''}</div>
      <dl class="kv-grid">
        <dt>Última vuelta OK</dt><dd style="color:${vivo ? 'var(--green)' : 'var(--red)'};">${tick || 'nunca'}</dd>
        <dt>Errores atajados (24hs)</dt><dd>${fmtNum(estado.errores_atajados_24hs)}</dd>
      </dl>
      ${estado.ultimo_error ? `
        <p class="help-text">Último error (${tiempoRelativo(estado.ultimo_error.at) || '—'}): ${escHtml(estado.ultimo_error.error.slice(0, 200))}</p>
      ` : ''}
    </div>
  `;
}

function freshTagGlobal(fecha, prefijo) {
  const t = tiempoRelativo(fecha);
  return t ? `<span style="font-size:12px; font-weight:500; color:var(--muted); text-transform:none;">${prefijo} ${t}</span>` : '';
}

async function _refrescarWorker() {
  const el = document.getElementById('card-worker');
  if (!el) return;
  const data = await fetchJson('/api/v1/worker/estado').catch(() => null);
  if (!data || !data.ok) return;
  el.outerHTML = _renderWorkerHtml(data.estado);
}

// v0.2.5 -- gráfico de barras Ingresos/Gastos/Saldo, mismo concepto que ya
// usa jarvis-trabajo/dashboard.html (cfo-balance-chart) pero con Chart.js
// vendorizado acá mismo (renderer/shared/vendor/chart.umd.min.js) en vez de
// la CDN que usa ese dashboard viejo -- NicOS nunca depende de la red para
// su interfaz. El guard `typeof Chart === 'undefined'` es el mismo patrón
// defensivo que ya usaba el dashboard viejo por si el script no cargó.
function _dibujarGraficoCfo(cfo) {
  const ctx = document.getElementById('cfo-chart');
  if (!ctx || typeof Chart === 'undefined') return;
  if (cfoChart) { cfoChart.destroy(); cfoChart = null; }

  const ingresos = cfo.total_ingresos_mes || 0;
  const gastos = cfo.total_gastos_mes || 0;
  const saldo = cfo.saldo_mes || 0;
  const colorSaldo = saldo >= 0 ? '#038139' : '#d22525'; // mismos tokens --green/--red de styles.css

  cfoChart = new Chart(ctx, {
    type: 'bar',
    data: {
      labels: ['Ingresos', 'Gastos', 'Saldo'],
      datasets: [{
        data: [ingresos, gastos, saldo],
        backgroundColor: ['#038139', '#d22525', colorSaldo],
        borderRadius: 6,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { ticks: { callback: (v) => '$' + fmtNum(v), font: { size: 10 } }, grid: { color: '#e2e6ec' } },
        x: { grid: { display: false }, ticks: { font: { size: 12 } } },
      },
    },
  });
}

async function loadResumen() {
  const el = document.getElementById('tab-resumen');
  el.innerHTML = '<div class="empty">Cargando...</div>';
  const data = await fetchJson('/director/summary');
  if (!data.ok) {
    el.innerHTML = `<div class="error-box">${escHtml(data.error || 'No se pudo cargar el resumen.')}</div>`;
    return;
  }
  const s = data.summary;
  const tb = s.trading_bot || {};
  const cons = s.consultorio || {};
  const cowork = s.cowork || {};
  const cfo = s.cfo_vivo || {};

  const freshTag = (fecha) => {
    const t = tiempoRelativo(fecha);
    return t ? `<span style="font-size:12px; font-weight:500; color:var(--muted); text-transform:none;">${t}</span>` : '';
  };

  el.innerHTML = `
    ${_renderAtencionHtml({ tareas: 0, whatsapp: 0, turnos: 0 })}
    ${_renderFaseCHtml([])}
    ${_renderAutoEnviadosHtml([])}
    ${_renderWorkerHtml({})}

    <div class="card">
      <h3>CFO Financiero</h3>
      ${cfo._missing ? '<div class="empty">Sin datos (falta cfo-vivo-resumen.json).</div>' : `
        <div class="row-between" style="align-items:flex-start;">
          <div class="metric-grid" style="flex:1;">
            <div class="metric"><div class="metric-label">Ingresos del mes</div><div class="metric-value">$${fmtNum(cfo.total_ingresos_mes)}</div></div>
            <div class="metric"><div class="metric-label">Gastos del mes</div><div class="metric-value">$${fmtNum(cfo.total_gastos_mes)}</div></div>
            <div class="metric"><div class="metric-label">Saldo del mes</div><div class="metric-value" style="color:${(cfo.saldo_mes || 0) >= 0 ? 'var(--green)' : 'var(--red)'};">$${fmtNum(cfo.saldo_mes)}</div></div>
          </div>
          ${freshTag(cfo.actualizado)}
        </div>
        <div class="chart-box"><canvas id="cfo-chart"></canvas></div>
        ${(cfo.ultimos_movimientos || []).length > 0 ? `
          <table style="margin-top:var(--space-3);">
            <tr><th>Fecha</th><th>Concepto</th><th>Monto</th></tr>
            ${cfo.ultimos_movimientos.slice(0, 5).map((m) => `
              <tr>
                <td>${escHtml(m.fecha)}</td>
                <td>${escHtml(m.concepto)}</td>
                <td style="color:${m.tipo === 'gasto' ? 'var(--red)' : 'var(--green)'};">${m.tipo === 'gasto' ? '-' : '+'}$${fmtNum(m.monto)}</td>
              </tr>
            `).join('')}
          </table>
        ` : ''}
      `}
    </div>

    <div class="card">
      <div class="row-between"><h3 style="margin:0;">Trading Bot</h3>${freshTag(tb.actualizado)}</div>
      ${tb._missing ? '<div class="empty">Sin datos (falta trading-bot-resumen.json).</div>' : `
        <div class="metric-grid">
          <div class="metric"><div class="metric-label">PnL total</div><div class="metric-value">${fmtNum(tb.pnl_total)} <span style="font-size:13px; font-weight:500; color:var(--muted);">USDT</span></div></div>
          <div class="metric"><div class="metric-label">Trades</div><div class="metric-value">${fmtNum(tb.trades)}</div></div>
          <div class="metric"><div class="metric-label">Win rate</div><div class="metric-value">${fmtNum(tb.win_rate)}%</div></div>
          <div class="metric"><div class="metric-label">Drawdown máx.</div><div class="metric-value">${escHtml(tb.drawdown_max)}</div></div>
        </div>
        <dl class="kv-grid" style="margin-top:16px;">
          <dt>Régimen</dt><dd>${escHtml(tb.regimen)}</dd>
          <dt>Última actividad</dt><dd>${escHtml(tb.ultima_actividad)}</dd>
        </dl>
      `}
    </div>

    <div class="card">
      <div class="row-between"><h3 style="margin:0;">Consultorio (agregado, sin datos de pacientes)</h3>${freshTag(cons.actualizado)}</div>
      ${cons._missing ? '<div class="empty">Sin datos (falta consultorio-resumen.json).</div>' : `
        <div class="metric-grid">
          <div class="metric"><div class="metric-label">Total consultas</div><div class="metric-value">${fmtNum(cons.total_consultas)}</div></div>
          <div class="metric"><div class="metric-label">Clínica general</div><div class="metric-value">${fmtNum(cons.clinica_general)}</div></div>
          <div class="metric"><div class="metric-label">Psiquiatría</div><div class="metric-value">${fmtNum(cons.psiquiatria)}</div></div>
          <div class="metric"><div class="metric-label">Pendientes DRAPP</div><div class="metric-value">${fmtNum(cons.pendientes_drapp)}</div></div>
        </div>
      `}
    </div>

    <div class="card">
      <h3>Proyectos Cowork</h3>
      ${(cowork.proyectos || []).length === 0 ? '<div class="empty">Sin datos.</div>' : `
        <table>
          <tr><th>Proyecto</th><th>Estado</th><th>Última actividad</th></tr>
          ${cowork.proyectos.map((p) => `
            <tr>
              <td>${escHtml(p.nombre)}</td>
              <td><span class="tag ${p.estado === 'activo' ? 'resuelto' : 'proceso'}">${escHtml(p.estado)}</span></td>
              <td>${escHtml(p.ultima_actividad)}</td>
            </tr>
          `).join('')}
        </table>
      `}
    </div>
  `;

  _dibujarGraficoCfo(cfo);
  await Promise.all([_refrescarAtencion(), _refrescarFaseC(), _refrescarAutoEnviados(), _refrescarWorker()]);
}

function renderChatLog() {
  const log = document.getElementById('chat-log');
  log.innerHTML = chatHistory.map((m) => `
    <div class="chat-msg ${m.role}">
      ${m.role === 'assistant' ? `<span class="brain-tag">${m.brain === 'openai' ? 'ChatGPT' : 'Claude'}</span>` : ''}
      ${escHtml(m.content)}
    </div>
  `).join('');
  log.scrollTop = log.scrollHeight;
}

function loadChat() {
  const el = document.getElementById('tab-chat');
  el.innerHTML = `
    <div class="card">
      <h3>Chat del Director</h3>
      <div class="row-wrap" style="margin-bottom:var(--space-3);">
        <label style="display:inline-flex; align-items:center; gap:6px; text-transform:none; font-weight:400; font-size:var(--text-base); color:var(--text); margin:0;">
          <input type="radio" name="brain" value="claude" checked style="width:auto;"> Claude
        </label>
        <label style="display:inline-flex; align-items:center; gap:6px; text-transform:none; font-weight:400; font-size:var(--text-base); color:var(--text); margin:0;">
          <input type="radio" name="brain" value="openai" style="width:auto;"> ChatGPT
        </label>
      </div>
      <div class="chat-log" id="chat-log"></div>
      <div class="chat-input-row">
        <input type="text" id="chat-input" placeholder="Preguntale algo al Director...">
        <button class="primary" id="chat-send">Enviar</button>
      </div>
    </div>
  `;
  renderChatLog();

  document.querySelectorAll('input[name="brain"]').forEach((r) => {
    r.addEventListener('change', (e) => { currentBrain = e.target.value; });
  });

  const send = async () => {
    const input = document.getElementById('chat-input');
    const message = input.value.trim();
    if (!message) return;
    input.value = '';
    chatHistory.push({ role: 'user', content: message });
    renderChatLog();

    const result = await fetchJson('/director/chat', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ message, brain: currentBrain, history: chatHistory }),
    });

    chatHistory.push({
      role: 'assistant',
      content: result.reply || 'Sin respuesta.',
      brain: result.brain || currentBrain,
    });
    renderChatLog();
  };

  document.getElementById('chat-send').addEventListener('click', send);
  document.getElementById('chat-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') send();
  });
}

async function updateApiBase(newPort) {
  // Se llama cuando Ajustes reinicia el sidecar (nuevo puerto efímero).
  // v0.2.6 -- hallazgo real: esto solo reiniciaba Tareas -- el resto de
  // las pestañas (Abate, Recordatorios, WhatsApp, Facturas, Mail) y la
  // insignia de "conectado" arriba quedaban pegadas al puerto viejo (ya
  // muerto) hasta recargar toda la ventana a mano, mostrando "sidecar no
  // responde" aunque el sidecar nuevo estuviera perfectamente sano.
  API = `http://127.0.0.1:${newPort}`;
  initTasksTab(API);
  initAbateTab(API);
  initRecordatoriosTab(API);
  initMensajesWhatsappTab(API, 'director');
  initFacturasTab(API);
  initMailTab(API, 'director');

  const statusEl = document.getElementById('server-status');
  try {
    const ping = await fetchJson('/ping');
    statusEl.textContent = ping.ok ? 'conectado' : 'error';
  } catch (e) {
    statusEl.textContent = 'sidecar no responde';
  }
}

function loadAjustes() {
  const el = document.getElementById('tab-ajustes');
  renderSettingsPanelDirector(el, API, updateApiBase);
}

async function init() {
  const statusEl = document.getElementById('server-status');
  let port = params.get('port');
  if (!port) port = await window.nicos.getSidecarPort();
  if (!port) {
    statusEl.textContent = 'sidecar no disponible';
    return;
  }
  API = `http://127.0.0.1:${port}`;

  try {
    const ping = await fetchJson('/ping');
    statusEl.textContent = ping.ok ? 'conectado' : 'error';
  } catch (e) {
    statusEl.textContent = 'sidecar no responde';
  }

  initTasksTab(API);
  initAbateTab(API);
  initRecordatoriosTab(API);
  initMensajesWhatsappTab(API, 'director');
  initFacturasTab(API);
  initMailTab(API, 'director');
  await loadResumen();
  await loadTasksTab();
  loadChat();
  loadAjustes();
  switchTab('resumen');

  setInterval(() => {
    // Si hay un detalle de tarea abierto, no se reconstruye toda la lista --
    // antes esto cerraba de golpe cualquier detalle abierto (y tiraba el
    // scroll arriba) cada 15s mientras alguien estaba mirando/por aprobar
    // algo. Solo se refresca sin nada abierto, o se actualiza el contador.
    const hayDetalleAbierto = !!document.querySelector('.task-detail[style*="display: block"]');
    if (document.getElementById('tab-tareas').style.display !== 'none' && !hayDetalleAbierto) {
      renderTasksList();
    } else {
      // igual actualizar el contador del badge aunque no estemos parados en la pestaña
      fetchJson('/api/v1/tasks?state=pending_approval').then((d) => {
        const badge = document.getElementById('badge-pending-count');
        if (badge && d.ok) badge.textContent = d.tasks.length > 0 ? `(${d.tasks.length})` : '';
      });
    }
    // Mismo cadencia para "Necesita tu atención" y los paneles nuevos de
    // Fase C del Resumen -- solo si esa pestaña está montada (cada
    // _refrescar* no hace nada si no lo está).
    if (document.getElementById('tab-resumen').style.display !== 'none') {
      _refrescarAtencion();
      _refrescarFaseC();
      _refrescarAutoEnviados();
      _refrescarWorker();
    }

    // v0.2.6 -- pedido real de Nicolás mientras probaba Fase C: había que
    // recargar la página a mano para ver una respuesta nueva del paciente
    // en la Bandeja. Mismo criterio que Tareas arriba: nunca reconstruir
    // la lista mientras hay un borrador siendo editado (perdería lo
    // tipeado), solo cuando la pestaña está visible y nada tiene foco.
    _refrescarSiVisibleYSinEdicion('tab-mensajes-whatsapp', _renderListaMensajesWhatsapp);
    _refrescarSiVisibleYSinEdicion('tab-mail', _renderListaMail);
    _refrescarSiVisibleYSinEdicion('tab-facturas', _renderListaFacturas);
    _refrescarSiVisibleYSinEdicion('tab-recordatorios', _renderListaRecordatorios);
  }, 15000);
}

function _refrescarSiVisibleYSinEdicion(tabId, renderFn) {
  const tab = document.getElementById(tabId);
  if (!tab || tab.style.display === 'none') return;
  const activo = document.activeElement;
  const editando = activo && tab.contains(activo) && ['TEXTAREA', 'INPUT', 'SELECT'].includes(activo.tagName);
  if (editando) return;
  renderFn();
}

init();
