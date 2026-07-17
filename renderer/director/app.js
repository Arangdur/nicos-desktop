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

function switchTab(name) {
  document.querySelectorAll('.tab-panel').forEach((el) => (el.style.display = 'none'));
  document.getElementById(`tab-${name}`).style.display = 'block';
}

document.querySelectorAll('[data-tab]').forEach((btn) => {
  btn.addEventListener('click', () => {
    switchTab(btn.dataset.tab);
    if (btn.dataset.tab === 'tareas') renderTasksList();
    if (btn.dataset.tab === 'ajustes') loadAjustes();
  });
});

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

  el.innerHTML = `
    <div class="card">
      <h3>Trading Bot</h3>
      ${tb._missing ? '<div class="empty">Sin datos (falta trading-bot-resumen.json).</div>' : `
        <table>
          <tr><td>PnL total</td><td>${fmtNum(tb.pnl_total)} USDT</td></tr>
          <tr><td>Trades</td><td>${fmtNum(tb.trades)}</td></tr>
          <tr><td>Win rate</td><td>${fmtNum(tb.win_rate)}%</td></tr>
          <tr><td>Drawdown máximo</td><td>${escHtml(tb.drawdown_max)}</td></tr>
          <tr><td>Régimen</td><td>${escHtml(tb.regimen)}</td></tr>
          <tr><td>Última actividad</td><td>${escHtml(tb.ultima_actividad)}</td></tr>
        </table>
      `}
    </div>

    <div class="card">
      <h3>Consultorio (agregado, sin datos de pacientes)</h3>
      ${cons._missing ? '<div class="empty">Sin datos (falta consultorio-resumen.json).</div>' : `
        <table>
          <tr><td>Total consultas</td><td>${fmtNum(cons.total_consultas)}</td></tr>
          <tr><td>Clínica general</td><td>${fmtNum(cons.clinica_general)}</td></tr>
          <tr><td>Psiquiatría</td><td>${fmtNum(cons.psiquiatria)}</td></tr>
          <tr><td>Pendientes DRAPP</td><td>${fmtNum(cons.pendientes_drapp)}</td></tr>
        </table>
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
      <div style="margin-bottom:10px;">
        <label style="display:inline-block; margin-right:12px;">
          <input type="radio" name="brain" value="claude" checked> Claude
        </label>
        <label style="display:inline-block;">
          <input type="radio" name="brain" value="openai"> ChatGPT
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

function loadAjustes() {
  const el = document.getElementById('tab-ajustes');
  renderSettingsPanelDirector(el, API);
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
  await loadResumen();
  await loadTasksTab();
  loadChat();
  loadAjustes();
  switchTab('resumen');

  setInterval(() => {
    if (document.getElementById('tab-tareas').style.display !== 'none') {
      renderTasksList();
    } else {
      // igual actualizar el contador del badge aunque no estemos parados en la pestaña
      fetchJson('/api/v1/tasks?state=pending_approval').then((d) => {
        const badge = document.getElementById('badge-pending-count');
        if (badge && d.ok) badge.textContent = d.tasks.length > 0 ? `(${d.tasks.length})` : '';
      });
    }
  }, 15000);
}

init();
