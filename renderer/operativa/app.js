// Vista Operativa (Marianela) — a diferencia de v0.1, esta app NO corre ningún
// sidecar local ni tiene una URL de API propia: todo pasa por window.nicos.operativa*
// (IPC hacia main.js, que le habla por HTTP+token a la Mac de Nicolás). Así es
// literalmente imposible que un secreto termine en esta máquina — el código
// de esta vista ni siquiera tiene un lugar donde guardarlo.

function escHtml(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
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
    if (btn.dataset.tab === 'entrada') loadMisTareas();
    if (btn.dataset.tab === 'calendario') loadCalendarioTab();
    if (btn.dataset.tab === 'recordatorios') loadRecordatoriosAvisos();
    if (btn.dataset.tab === 'mensajes-whatsapp') loadMensajesWhatsappTabOperativa();
    if (btn.dataset.tab === 'acerca-de') loadAcercaDe();
  });
});

function loadAcercaDe() {
  renderAboutPanel(document.getElementById('tab-acerca-de'));
}

function loadAjustes() {
  const el = document.getElementById('tab-ajustes');
  renderSettingsPanelOperativa(el);
}

async function init() {
  const statusEl = document.getElementById('server-status');
  statusEl.textContent = 'vinculado';

  // El header decía "Consultorio" fijo, sin importar quién está usando la
  // app -- ahora refleja el nombre real de la PERSONA que inició sesión
  // (v0.2.2: una PC puede tener varias identidades vinculadas, ver login.js).
  try {
    const identity = await window.nicos.identityActive();
    const subtitleEl = document.getElementById('banner-subtitle');
    if (subtitleEl && identity && identity.display_name) {
      subtitleEl.textContent = `Operativa · ${identity.display_name}`;
    }
  } catch (e) { /* no bloquea el resto de la vista si esto falla */ }

  await loadEntradaRapida();
  loadAjustes();
  switchTab('entrada');
  updateOutboxBadge();

  setInterval(() => {
    updateOutboxBadge();
    if (document.getElementById('tab-entrada').style.display !== 'none') loadMisTareas();
  }, 20000);
}

init();
