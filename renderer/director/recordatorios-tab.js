// Pestaña Recordatorios (vista Director) — carga manual de los turnos de
// Medicina General de mañana para que NicOS mande el WhatsApp ~24hs antes
// (Psiquiatría queda afuera a propósito, ya tiene su propio recordatorio
// nativo en DrApp -- ver sidecar/recordatorios.py). La carga es manual por
// ahora, a la espera de la documentación de la API de Cormos -- ver memoria
// de sesión del bot de WhatsApp.

let recordatoriosApiBase = null;
let recordatoriosFilaIdx = 0;

const ESTADO_TAG = {
  pendiente: { clase: 'proceso', label: 'Pendiente' },
  enviado: { clase: 'resuelto', label: 'Enviado' },
  sin_telefono: { clase: 'nuevo', label: 'Sin teléfono' },
  fallo_envio: { clase: 'nuevo', label: 'Falló el envío' },
};

function initRecordatoriosTab(apiBase) {
  recordatoriosApiBase = apiBase;
}

async function recordatoriosFetch(path, opts) {
  const res = await fetch(`${recordatoriosApiBase}${path}`, opts);
  return res.json();
}

async function loadRecordatoriosTab() {
  const el = document.getElementById('tab-recordatorios');
  recordatoriosFilaIdx = 0;
  el.innerHTML = `
    <div class="card">
      <h3>Recordatorio de turnos — Medicina General</h3>
      <p class="help-text">
        Cargá acá los turnos de mañana. Psiquiatría queda afuera -- ya tiene su propio
        recordatorio nativo en DrApp. El teléfono es opcional: si falta, Marianela lo va
        a ver marcado como pendiente de resolver, nunca se manda a ciegas.
      </p>
      <div id="recordatorios-filas">
        ${_filaTurno({}, 0)}
      </div>
      <div class="row-wrap" style="margin-top:var(--space-3);">
        <button class="secondary" id="btn-agregar-turno">+ Agregar turno</button>
        <button class="primary" id="btn-importar-turnos">Importar</button>
      </div>
    </div>

    <div class="card">
      <h3>Estado de los recordatorios</h3>
      <div id="recordatorios-lista"></div>
    </div>
  `;

  document.getElementById('btn-agregar-turno').addEventListener('click', () => {
    recordatoriosFilaIdx += 1;
    document.getElementById('recordatorios-filas')
      .insertAdjacentHTML('beforeend', _filaTurno({}, recordatoriosFilaIdx));
  });

  document.getElementById('btn-importar-turnos').addEventListener('click', async () => {
    const filas = document.querySelectorAll('.recordatorio-fila');
    const turnos = [];
    for (const fila of filas) {
      const paciente_nombre = fila.querySelector('.rec-nombre').value.trim();
      const fecha_turno = fila.querySelector('.rec-fecha').value;
      const hora_turno = fila.querySelector('.rec-hora').value;
      if (!paciente_nombre || !fecha_turno || !hora_turno) continue;
      turnos.push({
        paciente_nombre,
        telefono: fila.querySelector('.rec-telefono').value.trim() || null,
        fecha_turno,
        hora_turno,
        cobertura: fila.querySelector('.rec-cobertura').value.trim(),
        practica: fila.querySelector('.rec-practica').value.trim() || 'Medicina General',
      });
    }
    if (turnos.length === 0) { showToast('Cargá al menos un turno con nombre, fecha y hora.', 'error'); return; }

    const result = await recordatoriosFetch('/api/v1/recordatorios/importar', {
      method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ turnos }),
    });
    if (!result.ok) { showToast(result.error, 'error'); return; }
    showToast(`${result.importados} turno${result.importados === 1 ? '' : 's'} importado${result.importados === 1 ? '' : 's'}.`, 'success');
    await loadRecordatoriosTab();
  });

  await _renderListaRecordatorios();
}

function _filaTurno(t, idx) {
  return `
    <div class="recordatorio-fila row-wrap" style="margin-top:8px;" data-idx="${idx}">
      <input type="text" class="rec-nombre" aria-label="Nombre del paciente" placeholder="Nombre del paciente" value="${escHtml(t.paciente_nombre || '')}" style="flex:2;">
      <input type="text" class="rec-telefono" aria-label="Teléfono" placeholder="Teléfono (opcional)" value="${escHtml(t.telefono || '')}" style="flex:1;">
      <input type="date" class="rec-fecha" aria-label="Fecha del turno" value="${t.fecha_turno || ''}" style="flex:1;">
      <input type="time" class="rec-hora" aria-label="Hora del turno" value="${t.hora_turno || ''}" style="flex:1;">
      <input type="text" class="rec-cobertura" aria-label="Cobertura" placeholder="Cobertura" value="${escHtml(t.cobertura || '')}" style="flex:1;">
      <input type="text" class="rec-practica" aria-label="Práctica" placeholder="Práctica" value="${escHtml(t.practica || 'Medicina General')}" style="flex:1;">
    </div>
  `;
}

async function _renderListaRecordatorios() {
  const el = document.getElementById('recordatorios-lista');
  const data = await recordatoriosFetch('/api/v1/recordatorios');
  if (!data.ok) { el.innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`; return; }
  if (data.recordatorios.length === 0) {
    el.innerHTML = '<div class="empty">Ningún turno cargado todavía.</div>';
    return;
  }
  el.innerHTML = data.recordatorios.map((r) => {
    const tag = ESTADO_TAG[r.estado] || { clase: '', label: r.estado };
    return `
      <div class="row-between" style="padding:10px 0; border-bottom:1px solid var(--border);">
        <div>
          <div style="font-weight:600;">${escHtml(r.paciente_nombre)}</div>
          <div style="font-size:12px; color:var(--muted);">${escHtml(r.practica)} · ${r.fecha_turno} ${r.hora_turno}${r.telefono ? ' · ' + escHtml(r.telefono) : ''}</div>
        </div>
        <span class="tag ${tag.clase}">${tag.label}</span>
      </div>
    `;
  }).join('');
}
