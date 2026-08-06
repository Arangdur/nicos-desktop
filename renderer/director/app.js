// Vista Director — reusa el mismo estilo de loaders que jarvis-trabajo/dashboard.html
// (fetch a un servidor local, fmtNum/escHtml, badges de estado) para que evolucionar
// este código se sienta igual que evolucionar el dashboard existente.

const params = new URLSearchParams(window.location.search);
let API = null; // se arma una vez sabemos el puerto real del sidecar

let chatHistory = [];
let currentBrain = 'claude';

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
    if (btn.dataset.tab === 'abate') loadAbateTab();
    if (btn.dataset.tab === 'recordatorios') loadRecordatoriosTab();
    if (btn.dataset.tab === 'mensajes-whatsapp') loadMensajesWhatsappTab();
    if (btn.dataset.tab === 'ajustes') loadAjustes();
    if (btn.dataset.tab === 'acerca-de') loadAcercaDe();
  });
});

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
  const [tareas, mensajes, recordatorios] = await Promise.all([
    fetchJson('/api/v1/tasks?state=pending_approval').catch(() => null),
    fetchJson('/api/v1/whatsapp/mensajes').catch(() => null),
    fetchJson('/api/v1/recordatorios').catch(() => null),
  ]);
  return {
    tareas: tareas && tareas.ok ? tareas.tasks.length : 0,
    whatsapp: mensajes && mensajes.ok
      ? mensajes.mensajes.filter((m) => m.estado === 'borrador_generado' || m.estado === 'error_clasificacion').length
      : 0,
    turnos: recordatorios && recordatorios.ok
      ? recordatorios.recordatorios.filter((r) => r.estado === 'sin_telefono' || r.estado === 'fallo_envio').length
      : 0,
  };
}

function _renderAtencionHtml(counts) {
  const items = [
    { n: counts.tareas, label: counts.tareas === 1 ? 'tarea esperando tu aprobación' : 'tareas esperando tu aprobación', tab: 'tareas' },
    { n: counts.whatsapp, label: counts.whatsapp === 1 ? 'mensaje de WhatsApp con borrador esperando' : 'mensajes de WhatsApp con borrador esperando', tab: 'mensajes-whatsapp' },
    { n: counts.turnos, label: counts.turnos === 1 ? 'turno necesita atención (sin teléfono o falló el envío)' : 'turnos necesitan atención (sin teléfono o falló el envío)', tab: 'recordatorios' },
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

  await _refrescarAtencion();
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

function updateApiBase(newPort) {
  // Se llama cuando Ajustes reinicia el sidecar (nuevo puerto efímero) --
  // sin esto, Resumen/Tareas/Chat quedan pegados al puerto viejo (ya
  // muerto) hasta que se recargue toda la ventana a mano.
  API = `http://127.0.0.1:${newPort}`;
  initTasksTab(API);
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
    // Mismo cadencia para "Necesita tu atención" del Resumen -- solo si esa
    // pestaña está montada (_refrescarAtencion no hace nada si no lo está).
    if (document.getElementById('tab-resumen').style.display !== 'none') {
      _refrescarAtencion();
    }
  }, 15000);
}

init();
