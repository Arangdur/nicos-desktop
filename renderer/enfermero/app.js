// Vista Enfermero/a de Abate -- v0.2.3. Funcionalidad real (ya no mockup):
// novedades tal cual (sin IA) y administración de medicación, ambas via
// window.nicos.abate* (IPC -> operativa-client.js -> sidecar de la Mac de
// Nicolás, autenticado con el token de ESTA identidad). El tratamiento
// farmacológico es de SOLO LECTURA acá -- solo el Director lo edita (ver
// renderer/director/abate-tab.js) -- el enfermero tilda tomas y carga
// novedades, nunca cambia droga/dosis/horario.

const CAT_LABEL = { operativo: 'Operativo', clinico_conductual: 'Clínico-conductual', administrativo: 'Administrativo' };
const CAT_TAG_CLASS = { operativo: 'info', clinico_conductual: 'proceso', administrativo: '' };

let categoriaSeleccionada = null;
let residenteDetalleActivo = null;
let residentesCache = [];

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
    if (btn.dataset.tab === 'novedades') loadNovedadesTab();
    if (btn.dataset.tab === 'residentes') loadResidentesTab();
    if (btn.dataset.tab === 'acerca-de') loadAcercaDe();
  });
});

function loadAcercaDe() {
  renderAboutPanel(document.getElementById('tab-acerca-de'));
}

async function loadAjustes() {
  renderSettingsPanelOperativa(document.getElementById('tab-ajustes'));
}

// ---- Novedades (feed general, todos los residentes) ---------------------

async function loadNovedadesTab() {
  const el = document.getElementById('tab-novedades');
  if (residentesCache.length === 0) {
    const data = await window.nicos.abateListResidentes();
    residentesCache = data.ok ? data.residentes : [];
  }
  el.innerHTML = `
    <div class="card">
      <h3>Reportar novedad</h3>
      <label>Residente</label>
      <select id="residente-select">
        ${residentesCache.map((r) => `<option value="${r.residente_id}">${escHtml(r.nombre)}</option>`).join('')}
      </select>
      <label>Tipo de novedad</label>
      <div class="row-wrap">
        <button class="secondary cat-btn" data-cat="operativo">Operativo</button>
        <button class="secondary cat-btn" data-cat="clinico_conductual">Clínico-conductual</button>
        <button class="secondary cat-btn" data-cat="administrativo">Administrativo</button>
      </div>
      <label style="margin-top:var(--space-3);">Detalle</label>
      <textarea id="detalle-input" rows="3" placeholder="Describí la novedad tal cual -- se guarda así, sin que ninguna IA la procese."></textarea>
      <div class="row-wrap" style="margin-top:var(--space-3);">
        <button class="primary" id="btn-reportar">Reportar</button>
      </div>
    </div>

    <div class="card">
      <h3>Novedades recientes — todos los turnos</h3>
      <p class="help-text" style="margin-bottom:var(--space-3);">Continuidad de turno: ves lo que reportaron tus compañeros, con su nombre.</p>
      <div id="lista-novedades"></div>
    </div>
  `;

  document.querySelectorAll('.cat-btn').forEach((btn) => {
    btn.addEventListener('click', () => {
      categoriaSeleccionada = btn.dataset.cat;
      document.querySelectorAll('.cat-btn').forEach((b) => b.classList.toggle('primary', b === btn));
      document.querySelectorAll('.cat-btn').forEach((b) => b.classList.toggle('secondary', b !== btn));
    });
  });

  document.getElementById('btn-reportar').addEventListener('click', async () => {
    const residenteId = document.getElementById('residente-select').value;
    const texto = document.getElementById('detalle-input').value.trim();
    if (!residenteId) { showToast('No hay ningún residente cargado -- pedile a Nicolás que dé de alta al menos uno.', 'error'); return; }
    if (!categoriaSeleccionada) { showToast('Elegí un tipo de novedad.', 'error'); return; }
    if (!texto) { showToast('Escribí el detalle de la novedad.', 'error'); return; }
    const result = await window.nicos.abateCreateNovedad(residenteId, categoriaSeleccionada, texto);
    if (!result.ok) { showToast(result.error, 'error'); return; }
    document.getElementById('detalle-input').value = '';
    categoriaSeleccionada = null;
    document.querySelectorAll('.cat-btn').forEach((b) => { b.classList.remove('primary'); b.classList.add('secondary'); });
    showToast('Novedad reportada.', 'success');
    _renderNovedadesFeed();
  });

  await _renderNovedadesFeed();
}

async function _renderNovedadesFeed(residenteId) {
  const el = document.getElementById('lista-novedades');
  if (!el) return;
  const data = await window.nicos.abateListNovedades(residenteId);
  const novedades = data.ok ? data.novedades : [];
  el.innerHTML = novedades.map((n) => `
    <div style="border-left:2px solid var(--border); padding:0 0 16px 16px; margin-top:8px;">
      <div style="font-size:12px; color:var(--muted);">
        <span class="tag ${CAT_TAG_CLASS[n.categoria] || ''}" style="margin-right:6px;">${CAT_LABEL[n.categoria] || n.categoria}</span>
        ${escHtml(n.residente_nombre)} · ${escHtml(n.created_by_nombre)} · ${new Date(n.created_at + 'Z').toLocaleString('es-AR')}
      </div>
      <div style="font-size:14px; margin-top:4px;">${escHtml(n.texto)}</div>
    </div>
  `).join('') || '<div class="empty">Sin novedades todavía.</div>';
}

// ---- Residentes: lista + detalle (tratamiento + medicación + novedades) ---

async function loadResidentesTab() {
  residenteDetalleActivo = null;
  const el = document.getElementById('tab-residentes');
  el.innerHTML = `
    <div id="residentes-lista-zona">
      <div class="card">
        <h3>Residentes</h3>
        <p class="help-text" style="margin-bottom:var(--space-3);">Elegí un residente para ver su tratamiento y tildar la medicación de hoy.</p>
        <div id="lista-residentes"><div class="empty">Cargando...</div></div>
      </div>
    </div>
    <div id="residente-detalle-zona" style="display:none;"></div>
  `;

  const data = await window.nicos.abateListResidentes();
  residentesCache = data.ok ? data.residentes : [];
  const listaEl = document.getElementById('lista-residentes');
  if (residentesCache.length === 0) {
    listaEl.innerHTML = '<div class="empty">Nicolás todavía no dio de alta ningún residente.</div>';
    return;
  }

  const resumenes = await Promise.all(residentesCache.map(async (r) => {
    const [tratData, admData] = await Promise.all([
      window.nicos.abateGetTratamiento(r.residente_id),
      window.nicos.abateListAdministracionesHoy(r.residente_id),
    ]);
    const tratamiento = tratData.ok ? tratData.tratamiento : null;
    const totalTomas = tratamiento ? tratamiento.medicacion.reduce((acc, m) => acc + m.horarios.length, 0) : 0;
    const dadas = admData.ok ? admData.administraciones.length : 0;
    return { ...r, pendientes: Math.max(0, totalTomas - dadas) };
  }));

  listaEl.innerHTML = resumenes.map((r) => `
    <div class="persona-pick" data-id="${r.residente_id}" style="cursor:pointer; padding:12px; border:1px solid var(--border); border-radius:10px; margin-bottom:8px;">
      <div class="row-between">
        <b>${escHtml(r.nombre)}</b>
        ${r.pendientes > 0
          ? `<span class="tag nuevo">${r.pendientes} toma${r.pendientes === 1 ? '' : 's'} pendiente${r.pendientes === 1 ? '' : 's'}</span>`
          : '<span class="tag resuelto">Medicación al día</span>'}
      </div>
    </div>
  `).join('');
  listaEl.querySelectorAll('[data-id]').forEach((row) => {
    row.addEventListener('click', () => abrirDetalleResidente(row.dataset.id));
  });
}

function _horarioVencido(horario) {
  const [h, m] = horario.split(':').map(Number);
  const ahora = new Date();
  return (h * 60 + m) < (ahora.getHours() * 60 + ahora.getMinutes());
}

async function abrirDetalleResidente(residenteId) {
  residenteDetalleActivo = residenteId;
  const residente = residentesCache.find((r) => r.residente_id === residenteId);
  document.getElementById('residentes-lista-zona').style.display = 'none';
  const zona = document.getElementById('residente-detalle-zona');
  zona.style.display = 'block';
  zona.innerHTML = '<div class="card"><div class="empty">Cargando...</div></div>';

  const [tratData, admData, novData] = await Promise.all([
    window.nicos.abateGetTratamiento(residenteId),
    window.nicos.abateListAdministracionesHoy(residenteId),
    window.nicos.abateListNovedades(residenteId),
  ]);
  const tratamiento = tratData.ok ? tratData.tratamiento : null;
  const administraciones = admData.ok ? admData.administraciones : [];
  const novedades = novData.ok ? novData.novedades : [];

  zona.innerHTML = `
    <button class="secondary" id="btn-volver-residentes" style="margin-bottom:var(--space-3);">&larr; Volver a Residentes</button>

    <div class="card">
      <h3>${escHtml(residente ? residente.nombre : '')}</h3>
    </div>

    <div class="card">
      <h3>Tratamiento farmacológico — hoy</h3>
      <p class="help-text" style="margin-bottom:var(--space-3);">Tildá cada toma cuando la administrés. Queda registrado con tu nombre y la hora real.</p>
      <div id="medicacion-zona"></div>
    </div>

    <div class="card">
      <h3>Indicaciones y notas terapéuticas</h3>
      <p class="help-text">Solo Nicolás puede editar esto -- si hace falta un cambio, avisale directamente.</p>
      <div style="margin-top:8px; white-space:pre-wrap;">${escHtml((tratamiento && tratamiento.indicaciones) || 'Sin indicaciones cargadas todavía.')}</div>
    </div>

    <div class="card">
      <h3>Novedades de este residente</h3>
      <div id="detalle-novedades"></div>
    </div>
  `;

  document.getElementById('btn-volver-residentes').addEventListener('click', loadResidentesTab);

  document.getElementById('detalle-novedades').innerHTML = novedades.map((n) => `
    <div style="border-left:2px solid var(--border); padding:0 0 16px 16px; margin-top:8px;">
      <div style="font-size:12px; color:var(--muted);">
        <span class="tag ${CAT_TAG_CLASS[n.categoria] || ''}" style="margin-right:6px;">${CAT_LABEL[n.categoria] || n.categoria}</span>
        ${escHtml(n.created_by_nombre)} · ${new Date(n.created_at + 'Z').toLocaleString('es-AR')}
      </div>
      <div style="font-size:14px; margin-top:4px;">${escHtml(n.texto)}</div>
    </div>
  `).join('') || '<div class="empty">Sin novedades para este residente todavía.</div>';

  _renderMedicacion(tratamiento, administraciones);
}

function _renderMedicacion(tratamiento, administraciones) {
  const el = document.getElementById('medicacion-zona');
  if (!tratamiento || tratamiento.medicacion.length === 0) {
    el.innerHTML = '<div class="empty">Nicolás todavía no cargó un tratamiento para este residente.</div>';
    return;
  }
  const porClave = {};
  administraciones.forEach((a) => { porClave[`${a.droga}|${a.horario_previsto}`] = a; });

  el.innerHTML = tratamiento.medicacion.map((m) => `
    <div style="padding:12px 0; border-bottom:1px solid var(--border-soft);">
      <span style="font-weight:700; font-size:15px;">${escHtml(m.droga)}</span>
      <span style="font-size:13px; color:var(--muted); margin-left:6px;">${escHtml(m.dosis)}</span>
      <div class="row-wrap" style="margin-top:8px;">
        ${m.horarios.map((h) => {
          const key = `${m.droga}|${h}`;
          const registro = porClave[key];
          const vencido = !registro && _horarioVencido(h);
          const color = registro ? 'var(--green)' : vencido ? 'var(--red)' : 'var(--border)';
          const bg = registro ? 'var(--green-bg)' : vencido ? 'var(--red-bg)' : 'white';
          return `
            <div class="horario-chip" data-droga="${escHtml(m.droga)}" data-horario="${h}"
              style="border:1px solid ${color}; background:${bg}; border-radius:8px; padding:8px 12px; cursor:${registro ? 'default' : 'pointer'}; font-size:13px; text-align:center; min-width:100px;">
              <span style="font-weight:700; display:block;">${h}</span>
              <span style="font-size:11px; display:block; margin-top:2px; color:${registro ? 'var(--green)' : vencido ? 'var(--red)' : 'var(--muted)'};">
                ${registro ? `✓ ${escHtml(registro.administrado_by_nombre.split(' ')[0])}` : vencido ? 'Atrasada' : 'Pendiente'}
              </span>
            </div>
          `;
        }).join('')}
      </div>
    </div>
  `).join('');

  el.querySelectorAll('.horario-chip').forEach((chip) => {
    chip.addEventListener('click', async () => {
      const droga = chip.dataset.droga;
      const horario = chip.dataset.horario;
      const ok = await showConfirm('Confirmar administración', `${droga} · ${horario}`, { confirmLabel: 'Confirmar' });
      if (!ok) return;
      const result = await window.nicos.abateRegistrarAdministracion(residenteDetalleActivo, droga, horario);
      if (!result.ok) { showToast(result.error, 'error'); return; }
      showToast('Toma registrada.', 'success');
      abrirDetalleResidente(residenteDetalleActivo);
    });
  });
}

async function init() {
  try {
    const identity = await window.nicos.identityActive();
    const subtitleEl = document.getElementById('banner-subtitle');
    if (subtitleEl && identity && identity.display_name) {
      subtitleEl.textContent = `Enfermería de Abate · ${identity.display_name}`;
    }
  } catch (e) { /* no bloquea el resto de la vista si esto falla */ }

  await loadNovedadesTab();
  loadAjustes();
  switchTab('novedades');
}

init();
