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
// v0.2.6 -- Fase C: si el paciente ya confirmó por escrito, el turno se
// crea/cancela solo en DrApp ANTES de que nadie apruebe este mensaje (ver
// turnos_conversacion.py) -- este tag distingue esa tarjeta de un simple
// borrador/oferta, para que rechazarla no se confunda con "no pasó nada".
const ACCION_DRAPP_LABEL_OP = {
  turno_creado: 'Turno ya creado en DrApp',
  turno_cancelado: 'Turno ya cancelado en DrApp',
};
// v0.2.5 -- Impeccable P2 (re-critique): borrador_generado usaba el mismo
// tag rojo "nuevo" que Urgente/Para Nicolás -- un borrador de rutina se veía
// tan alarmante como un mensaje realmente urgente. Pasa a "proceso" (ámbar,
// mismo tono que "esperando tu aprobación" en Tareas): sigue destacando que
// hay algo para revisar, sin competir con el rojo real.
const ESTADO_MENSAJE_TAG_OP = {
  recibido: { clase: 'proceso', label: 'Procesando...' },
  borrador_generado: { clase: 'proceso', label: 'Borrador listo' },
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
        quedan para que los apruebe Nicolás. Un saludo puro ("hola", "buen día"), el acuse
        de un pedido de receta, y un mensaje de seguimiento sobre esa misma receta ya salen
        solos, sin pasar por acá -- los vas a ver marcados como enviados. La receta en sí
        seguís gestionándola vos, como siempre.
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
      // v0.2.5 -- Impeccable P2 (re-critique): "Aprobar y enviar" pide
      // confirmación, "Rechazar" no -- misma tarjeta, mismo nivel de
      // decisión final, se pareja.
      // v0.2.6 -- si accion_drapp está seteado, el turno ya se creó/canceló
      // de verdad en DrApp -- rechazar esto NO lo deshace, solo evita que
      // el paciente se entere por WhatsApp. Aviso explícito para no confundir.
      const avisoTexto = m.accion_drapp
        ? `El turno ya ${m.accion_drapp === 'turno_creado' ? 'se creó' : 'se canceló'} en DrApp -- rechazar esto NO lo deshace, solo evita que el paciente reciba este mensaje.`
        : 'El borrador se descarta, no se manda nada.';
      const ok = await showConfirm('Rechazar este mensaje', avisoTexto, { confirmLabel: 'Rechazar', danger: true });
      if (!ok) return;
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
          ${m.accion_drapp ? `<span class="tag resuelto">${ACCION_DRAPP_LABEL_OP[m.accion_drapp] || m.accion_drapp}</span>` : ''}
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
        ${detectarPrecioOCobertura(m.borrador_respuesta) ? `
          <div class="error-box" style="margin-top:var(--space-2);">
            Este borrador menciona un precio o una cobertura de obra social — confirmá que sea
            un dato real antes de aprobar, la IA no tiene acceso a esa información.
          </div>
        ` : ''}
        <label style="margin-top:var(--space-3);">${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}</label>
        <textarea class="texto-respuesta" rows="3" aria-label="${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}">${escHtml(m.borrador_respuesta || '')}</textarea>
        <div class="row-wrap" style="margin-top:var(--space-2);">
          <button class="primary btn-aprobar">Aprobar y enviar</button>
          <button class="secondary btn-rechazar">Rechazar</button>
        </div>
      ` : m.estado === 'aprobado_enviado' ? `
        <p style="margin-top:var(--space-2); font-size:13px; color:var(--muted);">
          ${m.resuelto_by === 'sistema' ? 'Se mandó solo (respuesta automática, sin nada para aprobar)' : 'Se mandó'}: "${escHtml(m.respuesta_final)}"
        </p>
      ` : ''}
    </div>
  `;
}
