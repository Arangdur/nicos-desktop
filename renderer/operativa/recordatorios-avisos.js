// Pestaña Recordatorios (vista Operativa) — solo lectura. Marianela ve qué
// turnos de Medicina General tienen recordatorio pendiente o necesitan su
// atención (sin teléfono cargado, o falló el envío) -- nunca carga ni edita
// nada acá, eso es Director-only (ver sidecar/recordatorios.py).

const RECORDATORIOS_ESTADO_TAG = {
  pendiente: { clase: 'proceso', label: 'Pendiente' },
  enviado: { clase: 'resuelto', label: 'Enviado' },
  sin_telefono: { clase: 'nuevo', label: 'Sin teléfono -- resolver a mano' },
  fallo_envio: { clase: 'nuevo', label: 'Falló el envío -- resolver a mano' },
};

async function loadRecordatoriosAvisos() {
  const el = document.getElementById('tab-recordatorios');
  el.innerHTML = '<div class="card"><div class="empty">Cargando...</div></div>';

  const data = await window.nicos.recordatoriosList();
  if (!data.ok) {
    el.innerHTML = `<div class="card"><div class="error-box">${escHtml(data.error)}</div></div>`;
    return;
  }

  const necesitanAtencion = data.recordatorios.filter((r) => r.estado === 'sin_telefono' || r.estado === 'fallo_envio');
  const resto = data.recordatorios.filter((r) => r.estado !== 'sin_telefono' && r.estado !== 'fallo_envio');

  el.innerHTML = `
    <div class="card">
      <h3>Necesitan tu atención</h3>
      <p class="help-text">Turnos sin teléfono cargado, o donde falló el envío del recordatorio -- avisale a Nicolás si hace falta corregir el dato.</p>
      <div id="recordatorios-atencion"></div>
    </div>
    <div class="card">
      <h3>Resto de los recordatorios</h3>
      <div id="recordatorios-resto"></div>
    </div>
  `;

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
