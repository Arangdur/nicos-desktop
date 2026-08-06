// Pestaña "Bandeja de WhatsApp" (vista Operativa) — v0.2.5. Mismo concepto que
// el lado Director (mensajes-whatsapp-tab.js), pero acá se llega por IPC vía
// window.nicos.whatsapp* (electron/operativa-client.js), no por fetch directo
// -- Marianela nunca habla directo con el sidecar, siempre a través del
// proceso principal de Electron con su token de dispositivo.
//
// El bloqueo de "requiere_profesional" es de verdad del lado del servidor
// (server.py -> mensajes_whatsapp.RequiereProfesional, 403) -- acá solo se
// oculta el botón para que Marianela ni lo intente, no es la seguridad real.

const CLASIFICACION_LABEL_OP = {
  turno_nuevo: 'Turno nuevo', cancelacion: 'Cancelación', reprogramacion: 'Reprogramación',
  consulta_general: 'Consulta general', receta: 'Receta', ambiguo: 'Ambiguo',
};
const ESTADO_MENSAJE_TAG_OP = {
  recibido: { clase: 'proceso', label: 'Procesando...' },
  borrador_generado: { clase: 'nuevo', label: 'Borrador listo' },
  error_clasificacion: { clase: 'nuevo', label: 'Sin borrador (falló la IA)' },
  aprobado_enviado: { clase: 'resuelto', label: 'Enviado' },
  rechazado: { clase: '', label: 'Rechazado' },
};

async function loadMensajesWhatsappTabOperativa() {
  const el = document.getElementById('tab-mensajes-whatsapp');
  el.innerHTML = `
    <div class="card">
      <h3>Bandeja de WhatsApp</h3>
      <p class="help-text">
        Mensajes de pacientes con un borrador de respuesta ya armado -- revisalo (podés
        editarlo) y aprobalo para que salga, o rechazalo. Los que tocan algo clínico
        quedan para que los apruebe Nicolás.
      </p>
    </div>
    <div id="mensajes-whatsapp-lista-op"></div>
  `;
  await _renderListaMensajesWhatsappOperativa();
}

async function _renderListaMensajesWhatsappOperativa() {
  const el = document.getElementById('mensajes-whatsapp-lista-op');
  el.innerHTML = '<div class="empty">Cargando...</div>';
  const data = await window.nicos.whatsappMensajesList();
  if (!data.ok) {
    el.innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  const mensajes = data.mensajes;
  if (mensajes.length === 0) {
    el.innerHTML = '<div class="empty">Ningún mensaje todavía.</div>';
    return;
  }

  el.innerHTML = mensajes.map((m) => _cardMensajeOperativa(m)).join('');

  mensajes.forEach((m) => {
    if (m.estado !== 'borrador_generado' && m.estado !== 'error_clasificacion') return;
    const card = document.getElementById(`mensaje-op-${m.id}`);
    const btnAprobar = card.querySelector('.btn-aprobar');
    const btnRechazar = card.querySelector('.btn-rechazar');
    const textarea = card.querySelector('.texto-respuesta');

    if (btnAprobar) {
      btnAprobar.addEventListener('click', async () => {
        const texto_final = textarea.value.trim();
        // v0.2.5 -- Impeccable P1: mismo fix que el lado Director -- ver
        // mensajes-whatsapp-tab.js de esa vista para el detalle completo.
        const ok = await showConfirm(
          'Enviar esta respuesta',
          `Se va a mandar por WhatsApp a ${escHtml(m.telefono)}:<br><br>"${escHtml(texto_final)}"`,
          { confirmLabel: 'Enviar' },
        );
        if (!ok) return;
        btnAprobar.disabled = true;
        btnAprobar.textContent = 'Enviando...';
        const result = await window.nicos.whatsappMensajeAprobar(m.id, texto_final);
        if (!result.ok) {
          showToast(result.error, 'error');
          btnAprobar.disabled = false;
          btnAprobar.textContent = 'Aprobar y enviar';
          return;
        }
        showToast('Respuesta enviada.', 'success');
        await _renderListaMensajesWhatsappOperativa();
      });
    }

    btnRechazar.addEventListener('click', async () => {
      btnRechazar.disabled = true;
      const result = await window.nicos.whatsappMensajeRechazar(m.id);
      if (!result.ok) { showToast(result.error, 'error'); btnRechazar.disabled = false; return; }
      showToast('Mensaje descartado, no se mandó nada.', 'default');
      await _renderListaMensajesWhatsappOperativa();
    });
  });
}

function _cardMensajeOperativa(m) {
  const tag = ESTADO_MENSAJE_TAG_OP[m.estado] || { clase: '', label: m.estado };
  const accionable = m.estado === 'borrador_generado' || m.estado === 'error_clasificacion';
  const bloqueado = m.requiere_profesional; // del lado Operativa, SIEMPRE bloqueado si es true

  return `
    <div class="card" id="mensaje-op-${m.id}" style="margin-bottom:var(--space-3);">
      <div class="row-between">
        <div>
          <div style="font-weight:600;">${escHtml(m.telefono)}</div>
          <div style="font-size:12px; color:var(--muted);">${new Date(m.recibido_at).toLocaleString('es-AR')}</div>
        </div>
        <div class="row-wrap">
          ${m.urgente ? '<span class="tag nuevo">Urgente</span>' : ''}
          ${m.clasificacion ? `<span class="tag proceso">${CLASIFICACION_LABEL_OP[m.clasificacion] || m.clasificacion}</span>` : ''}
          ${m.requiere_profesional ? '<span class="tag nuevo">Para Nicolás</span>' : ''}
          <span class="tag ${tag.clase}">${tag.label}</span>
        </div>
      </div>

      <p style="margin-top:var(--space-3); padding:10px; background:var(--bg-subtle); border-radius:6px;">
        "${escHtml(m.texto_original)}"
      </p>

      ${bloqueado ? `
        <p class="help-text" style="color:var(--red); margin-top:var(--space-2);">
          Este mensaje toca algo clínico -- queda para que lo revise Nicolás, vos no podés aprobarlo.
        </p>
      ` : accionable ? `
        <label style="margin-top:var(--space-3);">${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}</label>
        <textarea class="texto-respuesta" rows="3">${escHtml(m.borrador_respuesta || '')}</textarea>
        <div class="row-wrap" style="margin-top:var(--space-2);">
          <button class="primary btn-aprobar">Aprobar y enviar</button>
          <button class="secondary btn-rechazar">Rechazar</button>
        </div>
      ` : m.estado === 'aprobado_enviado' ? `
        <p style="margin-top:var(--space-2); font-size:13px; color:var(--muted);">
          Se mandó: "${escHtml(m.respuesta_final)}"
        </p>
      ` : ''}
    </div>
  `;
}
