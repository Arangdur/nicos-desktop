// Pestaña Abate — Enfermería (vista Director). Acá el Director da de alta
// residentes y fija el tratamiento farmacológico vigente (Enfermero-only para
// leerlo/tildar tomas, nunca para editarlo -- ver sidecar/abate_enfermeria.py).
// También puede ver (solo lectura) las novedades y administraciones que ya
// cargaron los enfermeros -- mismo canal que hoy usan Albano/Daniela, ver
// NICOS_MASTER_SCOPE.md §3.

let abateApiBase = null;
let abateResidenteActivo = null;

const CATEGORIA_LABEL = { operativo: 'Operativo', clinico_conductual: 'Clínico-conductual', administrativo: 'Administrativo' };

function initAbateTab(apiBase) {
  abateApiBase = apiBase;
}

async function abateFetch(path, opts) {
  const res = await fetch(`${abateApiBase}${path}`, opts);
  return res.json();
}

async function loadAbateTab() {
  const el = document.getElementById('tab-abate');
  el.innerHTML = `
    <div class="card">
      <div class="row-between">
        <h3 style="margin-bottom:0;">Residentes de Abate</h3>
        <button class="primary" id="btn-nuevo-residente">+ Agregar residente</button>
      </div>
      <div id="abate-nuevo-form" style="display:none; margin-top:var(--space-3);">
        <label>Nombre del residente</label>
        <input type="text" id="abate-nuevo-nombre" placeholder="Ej. Residente Demo">
        <div class="row-wrap" style="margin-top:var(--space-3);">
          <button class="primary" id="btn-crear-residente">Crear</button>
          <button class="secondary" id="btn-cancelar-residente">Cancelar</button>
        </div>
      </div>
      <div id="abate-lista-residentes" style="margin-top:var(--space-4);"></div>
    </div>
    <div id="abate-detalle-zona"></div>
  `;

  document.getElementById('btn-nuevo-residente').addEventListener('click', () => {
    document.getElementById('abate-nuevo-form').style.display = 'block';
  });
  document.getElementById('btn-cancelar-residente').addEventListener('click', () => {
    document.getElementById('abate-nuevo-form').style.display = 'none';
    document.getElementById('abate-nuevo-nombre').value = '';
  });
  document.getElementById('btn-crear-residente').addEventListener('click', async () => {
    const nombre = document.getElementById('abate-nuevo-nombre').value.trim();
    if (!nombre) { showToast('Falta el nombre.', 'error'); return; }
    const result = await abateFetch('/api/v1/abate/residentes', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ nombre }),
    });
    if (!result.ok) { showToast(result.error, 'error'); return; }
    document.getElementById('abate-nuevo-form').style.display = 'none';
    document.getElementById('abate-nuevo-nombre').value = '';
    showToast('Residente creado.', 'success');
    renderListaResidentes();
  });

  await renderListaResidentes();
}

async function renderListaResidentes() {
  const el = document.getElementById('abate-lista-residentes');
  const data = await abateFetch('/api/v1/abate/residentes');
  if (!data.ok) { el.innerHTML = `<div class="error-box">${data.error}</div>`; return; }
  if (data.residentes.length === 0) {
    el.innerHTML = '<div class="empty">Ningún residente cargado todavía.</div>';
    return;
  }
  el.innerHTML = data.residentes.map((r) => `
    <div class="persona-pick" data-id="${r.residente_id}" style="cursor:pointer; padding:12px; border:1px solid var(--border); border-radius:10px; margin-bottom:8px;">
      <div style="font-weight:600;">${r.nombre}</div>
    </div>
  `).join('');
  el.querySelectorAll('[data-id]').forEach((row) => {
    row.addEventListener('click', () => abrirDetalleResidente(row.dataset.id, row.querySelector('div').textContent));
  });
}

async function abrirDetalleResidente(residenteId, nombre) {
  abateResidenteActivo = residenteId;
  const zona = document.getElementById('abate-detalle-zona');
  zona.innerHTML = '<div class="card"><div class="empty">Cargando...</div></div>';

  const [tratamientoData, novedadesData] = await Promise.all([
    abateFetch(`/api/v1/abate/residentes/${residenteId}/tratamiento`),
    abateFetch(`/api/v1/abate/novedades?residente_id=${residenteId}`),
  ]);

  const tratamiento = tratamientoData.ok ? tratamientoData.tratamiento : null;
  const medicacion = tratamiento ? tratamiento.medicacion : [];
  const indicaciones = tratamiento ? tratamiento.indicaciones : '';

  zona.innerHTML = `
    <div class="card">
      <h3>${nombre} — Tratamiento farmacológico</h3>
      <p class="help-text">Esto es lo único que ve y usa el enfermero para tildar tomas -- solo vos lo editás.</p>
      <div id="abate-medicacion-filas">
        ${medicacion.map((m, i) => _filaMedicacion(m, i)).join('')}
      </div>
      <button class="secondary" id="btn-agregar-droga" style="margin-top:var(--space-3);">+ Agregar droga</button>

      <label style="margin-top:var(--space-4);">Indicaciones y notas terapéuticas</label>
      <textarea id="abate-indicaciones" rows="3">${escHtml(indicaciones)}</textarea>

      <button class="primary" id="btn-guardar-tratamiento" style="margin-top:var(--space-3);">Guardar tratamiento</button>
    </div>

    <div class="card">
      <h3>Novedades cargadas (solo lectura acá)</h3>
      <div id="abate-novedades-lista"></div>
    </div>
  `;

  _renderNovedades(novedadesData.ok ? novedadesData.novedades : []);

  document.getElementById('btn-agregar-droga').addEventListener('click', () => {
    const cont = document.getElementById('abate-medicacion-filas');
    const idx = cont.children.length;
    cont.insertAdjacentHTML('beforeend', _filaMedicacion({ droga: '', dosis: '', horarios: [] }, idx));
  });

  document.getElementById('btn-guardar-tratamiento').addEventListener('click', async () => {
    const filas = document.querySelectorAll('.abate-fila-droga');
    const nuevaMedicacion = [];
    for (const fila of filas) {
      const droga = fila.querySelector('.abate-droga').value.trim();
      const dosis = fila.querySelector('.abate-dosis').value.trim();
      const horarios = fila.querySelector('.abate-horarios').value.split(',').map((h) => h.trim()).filter(Boolean);
      if (droga && dosis && horarios.length > 0) nuevaMedicacion.push({ droga, dosis, horarios });
    }
    const result = await abateFetch(`/api/v1/abate/residentes/${residenteId}/tratamiento`, {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ medicacion: nuevaMedicacion, indicaciones: document.getElementById('abate-indicaciones').value }),
    });
    if (!result.ok) { showToast(result.error, 'error'); return; }
    showToast('Tratamiento guardado.', 'success');
    abrirDetalleResidente(residenteId, nombre);
  });
}

function _filaMedicacion(m, idx) {
  return `
    <div class="abate-fila-droga row-wrap" style="margin-top:8px;" data-idx="${idx}">
      <input type="text" class="abate-droga" aria-label="Droga" placeholder="Droga" value="${escHtml(m.droga)}" style="flex:2;">
      <input type="text" class="abate-dosis" aria-label="Dosis" placeholder="Dosis (ej. 0.5mg)" value="${escHtml(m.dosis)}" style="flex:1;">
      <input type="text" class="abate-horarios" aria-label="Horarios" placeholder="Horarios, separados por coma (ej. 08:00, 20:00)" value="${(m.horarios || []).join(', ')}" style="flex:2;">
    </div>
  `;
}

function _renderNovedades(novedades) {
  const el = document.getElementById('abate-novedades-lista');
  if (novedades.length === 0) {
    el.innerHTML = '<div class="empty">Sin novedades cargadas todavía.</div>';
    return;
  }
  el.innerHTML = novedades.map((n) => `
    <div style="padding:10px 0; border-bottom:1px solid var(--border);">
      <div class="row-between">
        <span class="tag info">${CATEGORIA_LABEL[n.categoria] || n.categoria}</span>
        <span style="font-size:12px; color:var(--muted);">${n.created_by_nombre} · ${new Date(n.created_at + 'Z').toLocaleString('es-AR')}</span>
      </div>
      <div style="margin-top:4px;">${escHtml(n.texto)}</div>
    </div>
  `).join('');
}
