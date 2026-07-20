// Vista Enfermero/a de Abate -- v0.2.2. Tu identidad y tu vinculación con la
// Mac de Nicolás YA son reales (mismo mecanismo que Operativa: token emitido
// al completar el alta, ver login.js/pairing.py). Lo que todavía NO está
// construido es la pantalla de carga de novedades y administración de
// medicación -- existe como mockup validado (mockups/enfermeria-abate.html)
// pero no como funcionalidad real todavía. Esta pantalla lo dice tal cual,
// en vez de mostrar datos inventados o quedar en blanco.

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
    if (btn.dataset.tab === 'acerca-de') loadAcercaDe();
  });
});

function loadAcercaDe() {
  renderAboutPanel(document.getElementById('tab-acerca-de'));
}

function loadNovedades() {
  const el = document.getElementById('tab-novedades');
  el.innerHTML = `
    <div class="card">
      <h3>Todavía en construcción</h3>
      <p class="help-text" style="margin-top:var(--space-2);">
        Tu cuenta ya es real -- tu acceso a esta PC está vinculado de verdad
        con la Mac de Nicolás. Lo que falta construir es la pantalla de carga
        de novedades y de administración de medicación (el diseño ya está
        validado, la funcionalidad real llega en la próxima actualización).
      </p>
      <p class="help-text" style="margin-top:var(--space-2);">
        Cuando esté lista, vas a poder cargar novedades tal cual las escribas
        (sin que ninguna IA las procese) y tildar cada toma de medicación a
        medida que la administrás.
      </p>
    </div>
  `;
}

async function loadAjustes() {
  renderSettingsPanelOperativa(document.getElementById('tab-ajustes'));
}

async function init() {
  try {
    const identity = await window.nicos.identityActive();
    const subtitleEl = document.getElementById('banner-subtitle');
    if (subtitleEl && identity && identity.display_name) {
      subtitleEl.textContent = `Enfermería de Abate · ${identity.display_name}`;
    }
  } catch (e) { /* no bloquea el resto de la vista si esto falla */ }

  loadNovedades();
  loadAjustes();
  switchTab('novedades');
}

init();
