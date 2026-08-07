// Entrada rápida — lo que pidió Nicolás como caso de uso central: Marianela
// escribe en lenguaje natural, sin comandos, sin saber si es CFO o Abate.
// Si la Mac está desconectada, queda en el outbox local (sin ningún dato
// sensible, solo el texto) y se reintenta sola cuando vuelve la conexión.

async function loadEntradaRapida() {
  const el = document.getElementById('tab-entrada');
  el.innerHTML = `
    <div class="card">
      <h3>Registrar algo</h3>
      <p class="help-text">
        Escribí con tus palabras — por ejemplo "Registrar pago de electricidad de Abate por $450.000"
        o "Cargá un ingreso de $1.200.000 de Hospital Posse". NicOS decide a dónde va.
      </p>
      <textarea id="entrada-texto" rows="3" aria-label="Registrar algo" placeholder="Contame qué pasó..."></textarea>
      <div id="entrada-status" style="font-size:var(--text-sm); margin:var(--space-3) 0;"></div>
      <button class="primary" id="btn-enviar-entrada">Enviar</button>
    </div>
    <div class="card">
      <h3>Mis últimas tareas enviadas</h3>
      <div id="mis-tareas-list"></div>
    </div>
  `;

  document.getElementById('btn-enviar-entrada').addEventListener('click', async () => {
    const textoEl = document.getElementById('entrada-texto');
    const texto = textoEl.value.trim();
    const statusEl = document.getElementById('entrada-status');
    if (!texto) return;

    const clinico = detectClinicalData(texto);
    if (clinico.blocked) {
      statusEl.textContent = clinico.reason + ' NicOS no maneja datos de pacientes — consultale a Nicolás el canal correcto.';
      statusEl.style.color = 'var(--red)';
      return;
    }

    statusEl.textContent = 'Enviando...';
    statusEl.style.color = 'var(--muted)';
    const result = await window.nicos.operativaSubmitTask(texto);

    if (result.queued) {
      statusEl.textContent = result.message;
      statusEl.style.color = 'var(--amber)';
    } else if (result.ok) {
      statusEl.textContent = 'Enviado. Podés ver el estado abajo.';
      statusEl.style.color = 'var(--green)';
      textoEl.value = '';
    } else {
      statusEl.textContent = 'Error: ' + (result.error || 'desconocido');
      statusEl.style.color = 'var(--red)';
    }
    updateOutboxBadge();
    loadMisTareas();
  });

  loadMisTareas();
}

async function loadMisTareas() {
  const el = document.getElementById('mis-tareas-list');
  if (!el) return;
  const result = await window.nicos.operativaListTasks();
  // v0.2.5 -- Impeccable P1 (re-critique): antes el badge del header decía
  // "vinculado" una sola vez al arrancar y nunca más -- no reflejaba si la
  // Mac seguía realmente alcanzable. Esta es la misma llamada que ya
  // distingue offline de verdad (operativa-client.js), se reusa acá.
  const badge = document.getElementById('server-status');
  if (badge) badge.textContent = result.offline ? 'sin conexión con la Mac' : 'vinculado';
  if (result.offline) {
    el.innerHTML = `<div class="error-box">${result.error}</div>`;
    return;
  }
  if (!result.ok) {
    el.innerHTML = `<div class="error-box">${result.error || 'No se pudo cargar.'}</div>`;
    return;
  }
  if (result.tasks.length === 0) {
    el.innerHTML = '<div class="empty">Todavía no enviaste nada.</div>';
    return;
  }
  const ESTADO_LABEL = {
    received: 'Recibida', parsing: 'Procesando', classified: 'Clasificada',
    needs_information: 'Falta información', pending_approval: 'Esperando aprobación de Nicolás',
    ready: 'Lista', executing: 'Ejecutando', completed: 'Completada',
    failed: 'Falló', needs_review: 'Nicolás la va a revisar', cancelled: 'Cancelada',
  };
  const ESTADO_TAG_CLASS = {
    pending_approval: 'proceso', needs_information: 'proceso', needs_review: 'proceso',
    completed: 'resuelto', ready: 'resuelto', executing: 'resuelto',
    failed: 'nuevo', cancelled: 'nuevo',
  };
  el.innerHTML = result.tasks.slice(0, 10).map((t) => `
    <div class="row" style="padding:var(--space-2) 0; border-bottom:1px solid var(--border-soft); font-size:var(--text-sm);">
      <span class="tag ${ESTADO_TAG_CLASS[t.state] || 'proceso'}">${ESTADO_LABEL[t.state] || t.state}</span>
      <span>${escHtml(t.raw_text)}</span>
    </div>
  `).join('');
}

async function updateOutboxBadge() {
  const count = await window.nicos.operativaOutboxCount();
  const badge = document.getElementById('badge-outbox');
  if (badge) badge.textContent = count > 0 ? `(${count} en espera)` : '';
}
