// Pestaña Recordatorios (vista Operativa) — v0.2.6. Antes era de solo
// lectura (Marianela solo veía qué faltaba resolver, sin poder hacer nada
// desde acá) -- feedback real de uso: "así como está no sirve para nada".
// Ahora Marianela puede cargar turnos nuevos y completar el teléfono de uno
// que quedó 'sin_telefono' -- carga administrativa de agenda, no clínica,
// mismo criterio que ya se usa en Bandeja de WhatsApp (ver
// sidecar/recordatorios.py). El envío del recordatorio en sí sigue siendo
// 100% automático (worker.py), esto no cambia.

let recordatoriosFilaIdxOp = 0;

const RECORDATORIOS_ESTADO_TAG = {
  pendiente: { clase: 'proceso', label: 'Pendiente' },
  enviado: { clase: 'resuelto', label: 'Enviado' },
  sin_telefono: { clase: 'nuevo', label: 'Sin teléfono -- resolver a mano' },
  fallo_envio: { clase: 'nuevo', label: 'Falló el envío -- resolver a mano' },
};

async function loadRecordatoriosAvisos() {
  const el = document.getElementById('tab-recordatorios');
  recordatoriosFilaIdxOp = 0;
  el.innerHTML = `
    <div class="card">
      <h3>Cargar turno de mañana</h3>
      <p class="help-text">
        Psiquiatría no va acá -- ya tiene su propio recordatorio en DrApp. El teléfono es
        opcional: si falta, el turno queda marcado como "sin teléfono" para completarlo después.
      </p>
      <div id="recordatorios-filas-op">
        ${_filaTurnoOp({}, 0)}
      </div>
      <div class="row-wrap" style="margin-top:var(--space-3);">
        <button class="secondary" id="btn-agregar-turno-op">+ Agregar turno</button>
        <button class="primary" id="btn-importar-turnos-op">Cargar</button>
      </div>
    </div>
    <div class="card">
      <h3>Necesitan tu atención</h3>
      <p class="help-text">Turnos sin teléfono cargado, o donde falló el envío del recordatorio.</p>
      <div id="recordatorios-atencion"></div>
    </div>
    <div class="card">
      <h3>Resto de los recordatorios</h3>
      <div id="recordatorios-resto"></div>
    </div>
  `;

  document.getElementById('btn-agregar-turno-op').addEventListener('click', () => {
    recordatoriosFilaIdxOp += 1;
    document.getElementById('recordatorios-filas-op')
      .insertAdjacentHTML('beforeend', _filaTurnoOp({}, recordatoriosFilaIdxOp));
  });

  document.getElementById('btn-importar-turnos-op').addEventListener('click', async () => {
    const filas = document.querySelectorAll('.recordatorio-fila-op');
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

    const result = await window.nicos.recordatoriosImportar(turnos);
    if (!result.ok) { showToast(result.error, 'error'); return; }
    showToast(`${result.importados} turno${result.importados === 1 ? '' : 's'} cargado${result.importados === 1 ? '' : 's'}.`, 'success');
    await loadRecordatoriosAvisos();
  });

  await _renderListasRecordatorios();
}

function _filaTurnoOp(t, idx) {
  return `
    <div class="recordatorio-fila-op row-wrap" style="margin-top:8px;" data-idx="${idx}">
      <input type="text" class="rec-nombre" aria-label="Nombre del paciente" placeholder="Nombre del paciente" value="${escHtml(t.paciente_nombre || '')}" style="flex:2;">
      <input type="text" class="rec-telefono" aria-label="Teléfono" placeholder="Teléfono (opcional)" value="${escHtml(t.telefono || '')}" style="flex:1;">
      <input type="date" class="rec-fecha" aria-label="Fecha del turno" value="${t.fecha_turno || ''}" style="flex:1;">
      <input type="time" class="rec-hora" aria-label="Hora del turno" value="${t.hora_turno || ''}" style="flex:1;">
      <input type="text" class="rec-cobertura" aria-label="Cobertura" placeholder="Cobertura" value="${escHtml(t.cobertura || '')}" style="flex:1;">
      <input type="text" class="rec-practica" aria-label="Práctica" placeholder="Práctica" value="${escHtml(t.practica || 'Medicina General')}" style="flex:1;">
    </div>
  `;
}

async function _renderListasRecordatorios() {
  const data = await window.nicos.recordatoriosList();
  if (!data.ok) {
    document.getElementById('recordatorios-atencion').innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  const necesitanAtencion = data.recordatorios.filter((r) => r.estado === 'sin_telefono' || r.estado === 'fallo_envio');
  const resto = data.recordatorios.filter((r) => r.estado !== 'sin_telefono' && r.estado !== 'fallo_envio');

  _renderLista('recordatorios-atencion', necesitanAtencion, 'Nada pendiente de tu atención.');
  _renderLista('recordatorios-resto', resto, 'Ningún turno cargado todavía.');
}

function _renderLista(elId, lista, vacioTexto) {
  const el = document.getElementById(elId);
  if (lista.length === 0) {
    el.innerHTML = `<div class="empty">${vacioTexto}</div>`;
    return;
  }
  el.innerHTML = lista.map((r) => {
    const tag = RECORDATORIOS_ESTADO_TAG[r.estado] || { clase: '', label: r.estado };
    return `
      <div style="padding:10px 0; border-bottom:1px solid var(--border);">
        <div class="row-between">
          <div>
            <div style="font-weight:600;">${escHtml(r.paciente_nombre)}</div>
            <div style="font-size:12px; color:var(--muted);">${escHtml(r.practica)} · ${r.fecha_turno} ${r.hora_turno}${r.telefono ? ' · ' + escHtml(r.telefono) : ''}</div>
          </div>
          <span class="tag ${tag.clase}">${tag.label}</span>
        </div>
        ${r.estado === 'sin_telefono' ? `
          <div class="row-wrap" style="margin-top:8px;">
            <input type="text" class="rec-completar-telefono" aria-label="Teléfono para ${escHtml(r.paciente_nombre)}" placeholder="Teléfono" style="flex:1;">
            <button class="secondary btn-completar-telefono" data-id="${r.id}">Completar</button>
          </div>
        ` : ''}
      </div>
    `;
  }).join('');

  el.querySelectorAll('.btn-completar-telefono').forEach((btn) => {
    btn.addEventListener('click', async () => {
      const input = btn.parentElement.querySelector('.rec-completar-telefono');
      const telefono = input.value.trim();
      if (!telefono) { showToast('Cargá un teléfono.', 'error'); return; }
      btn.disabled = true;
      const result = await window.nicos.recordatoriosCompletarTelefono(btn.dataset.id, telefono);
      if (!result.ok) { showToast(result.error, 'error'); btn.disabled = false; return; }
      showToast('Teléfono completado.', 'success');
      await _renderListasRecordatorios();
    });
  });
}
