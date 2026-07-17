// Vista Operativa (Marianela) — tabla de mensajes de la hoja "Bot WhatsApp Consultorio",
// equivalente funcional al dashboard de Google Apps Script que ya usa hoy (ver
// jarvis-trabajo/manual_marianela.md para el esquema de columnas y los tipos de mensaje).

const params = new URLSearchParams(window.location.search);
let API = null;
let allMessages = [];
let currentFilter = 'todos';

const TIPOS_LABEL = {
  TURNO: 'Turno', RECETA: 'Receta', CANCELACION: 'Cancelación', CERTIFICADO: 'Certificado',
  ESTADO_RECETA: 'Estado receta', ELECTROCARDIOGRAMA: 'ECG', FICHA_MEDICA: 'Ficha médica',
  PRECIO: 'Precio', AMBIGUO: 'Ambiguo',
};

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
  btn.addEventListener('click', () => switchTab(btn.dataset.tab));
});

function estadoTagClass(estado) {
  const e = (estado || '').toLowerCase();
  if (e.includes('nuevo')) return 'nuevo';
  if (e.includes('proceso')) return 'proceso';
  if (e.includes('resuelto')) return 'resuelto';
  return 'proceso';
}

function applyFilter(messages) {
  if (currentFilter === 'todos') return messages;
  if (currentFilter === 'urgente') return messages.filter((m) => (m.Urgente || '').toLowerCase().startsWith('s'));
  return messages.filter((m) => estadoTagClass(m.Estado) === currentFilter);
}

function renderTable() {
  const el = document.getElementById('mensajes-tabla');
  const rows = applyFilter(allMessages);
  if (rows.length === 0) {
    el.innerHTML = '<div class="empty">No hay mensajes con este filtro.</div>';
    return;
  }
  el.innerHTML = `
    <table>
      <tr>
        <th>Fecha</th><th>Tipo</th><th>Teléfono</th><th>Mensaje</th><th>Estado</th>
        <th>Nombre</th><th>Apellido</th><th>Obra Social</th><th>Urgente</th><th></th>
      </tr>
      ${rows.map((m) => `
        <tr data-row="${m._row}">
          <td>${escHtml(m.Fecha)}</td>
          <td>${escHtml(TIPOS_LABEL[m.Tipo] || m.Tipo)}</td>
          <td>${escHtml(m.Telefono)}</td>
          <td style="max-width:220px;">${escHtml(m.Notas)}</td>
          <td>
            <select class="edit-estado">
              <option value="Nuevo" ${m.Estado === 'Nuevo' ? 'selected' : ''}>Nuevo</option>
              <option value="En proceso" ${m.Estado === 'En proceso' ? 'selected' : ''}>En proceso</option>
              <option value="Resuelto" ${m.Estado === 'Resuelto' ? 'selected' : ''}>Resuelto</option>
            </select>
          </td>
          <td><input type="text" class="edit-nombre" value="${escHtml(m.Nombre)}"></td>
          <td><input type="text" class="edit-apellido" value="${escHtml(m.Apellido)}"></td>
          <td><input type="text" class="edit-obrasocial" value="${escHtml(m['Obra Social'])}"></td>
          <td>
            <select class="edit-urgente">
              <option value="" ${!m.Urgente ? 'selected' : ''}>No</option>
              <option value="Si" ${m.Urgente === 'Si' ? 'selected' : ''}>Sí</option>
            </select>
          </td>
          <td><button class="secondary btn-guardar-fila">Guardar</button></td>
        </tr>
      `).join('')}
    </table>
  `;

  el.querySelectorAll('.btn-guardar-fila').forEach((btn) => {
    btn.addEventListener('click', async (e) => {
      const tr = e.target.closest('tr');
      const row = tr.dataset.row;
      const updates = {
        Estado: tr.querySelector('.edit-estado').value,
        Nombre: tr.querySelector('.edit-nombre').value,
        Apellido: tr.querySelector('.edit-apellido').value,
        'Obra Social': tr.querySelector('.edit-obrasocial').value,
        Urgente: tr.querySelector('.edit-urgente').value,
      };
      btn.textContent = 'Guardando...';
      const result = await fetchJson('/whatsapp/messages/update', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ row, updates }),
      });
      btn.textContent = result.ok ? 'Guardado ✓' : 'Error';
      if (result.ok) await loadMensajes();
    });
  });
}

async function loadMensajes() {
  const el = document.getElementById('tab-mensajes');
  if (!el.querySelector('#mensajes-tabla')) {
    el.innerHTML = `
      <div class="card">
        <h3>Filtros</h3>
        <div style="display:flex; gap:8px; flex-wrap:wrap;">
          <button class="secondary" data-filter="todos">Todos</button>
          <button class="secondary" data-filter="nuevo">Sin resolver</button>
          <button class="secondary" data-filter="urgente">Urgentes</button>
          <button class="secondary" data-filter="resuelto">Resueltos</button>
        </div>
      </div>
      <div class="card"><div id="mensajes-tabla"></div></div>
    `;
    el.querySelectorAll('[data-filter]').forEach((btn) => {
      btn.addEventListener('click', () => {
        currentFilter = btn.dataset.filter;
        renderTable();
      });
    });
  }

  document.getElementById('mensajes-tabla').innerHTML = '<div class="empty">Cargando...</div>';
  const data = await fetchJson('/whatsapp/messages');
  if (!data.ok) {
    document.getElementById('mensajes-tabla').innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  allMessages = data.messages;
  renderTable();
}

function loadAjustes() {
  const el = document.getElementById('tab-ajustes');
  renderSettingsPanel(el);
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

  await loadMensajes();
  loadAjustes();
  switchTab('mensajes');

  setInterval(loadMensajes, 60000);
}

init();
