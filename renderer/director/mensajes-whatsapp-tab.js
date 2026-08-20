// Pestaña "Bandeja de WhatsApp" (vista Director) — v0.2.5. Acá se aprueban o
// rechazan los borradores que la IA arma para cada mensaje entrante de un
// paciente. Nunca se manda nada sin este paso -- ver sidecar/mensajes_whatsapp.py.
// Mismo estilo que recordatorios-tab.js.

let mensajesWhatsappApiBase = null;
let mensajesWhatsappRol = 'director';

const CLASIFICACION_LABEL = {
  turno_nuevo: 'Turno nuevo', cancelacion: 'Cancelación', reprogramacion: 'Reprogramación',
  consulta_general: 'Consulta general', receta: 'Receta', ambiguo: 'Ambiguo',
};
// v0.2.6 -- Fase C: si el paciente ya confirmó por escrito, el turno se
// crea/cancela solo en DrApp ANTES de que nadie apruebe este mensaje (ver
// turnos_conversacion.py) -- este tag distingue esa tarjeta de un simple
// borrador/oferta, para que rechazarla no se confunda con "no pasó nada".
const ACCION_DRAPP_LABEL = {
  turno_creado: 'Turno ya creado en DrApp',
  turno_cancelado: 'Turno ya cancelado en DrApp',
};
// v0.2.5 -- Impeccable P2 (re-critique): mismo fix que operativa/mensajes-whatsapp-tab.js.
const ESTADO_MENSAJE_TAG = {
  recibido: { clase: 'proceso', label: 'Procesando...' },
  borrador_generado: { clase: 'proceso', label: 'Borrador listo' },
  error_clasificacion: { clase: 'nuevo', label: 'Sin borrador (falló la IA)' },
  aprobado_enviado: { clase: 'resuelto', label: 'Enviado' },
  rechazado: { clase: '', label: 'Rechazado' },
};

function initMensajesWhatsappTab(apiBase, rol) {
  mensajesWhatsappApiBase = apiBase;
  mensajesWhatsappRol = rol || 'director';
}

async function mensajesWhatsappFetch(path, opts) {
  const res = await fetch(`${mensajesWhatsappApiBase}${path}`, opts);
  return res.json();
}

async function loadMensajesWhatsappTab() {
  const el = document.getElementById('tab-mensajes-whatsapp');
  el.innerHTML = `
    <div class="card">
      <h3>Bandeja de WhatsApp</h3>
      <p class="help-text">
        Cada mensaje de un paciente pasa primero por acá — la IA arma un borrador de
        respuesta, vos lo revisás (editable) y recién ahí se manda. Dos excepciones se
        mandan directo, para que la respuesta sea rápida: un saludo puro ("hola", "buen día"),
        y el acuse de un pedido de receta ("recibimos tu pedido, ya lo derivamos") — la
        receta en sí sigue gestionándose como siempre, esto no emite ni aprueba nada clínico.
        Cualquier otra cosa (turno, urgencia, algo clínico más allá del acuse) siempre espera
        tu aprobación.
      </p>
    </div>
    <div id="mensajes-whatsapp-lista"></div>
  `;
  await _renderListaMensajesWhatsapp();
}

async function _renderListaMensajesWhatsapp() {
  const el = document.getElementById('mensajes-whatsapp-lista');
  el.innerHTML = '<div class="empty">Cargando...</div>';
  const data = await mensajesWhatsappFetch('/api/v1/whatsapp/mensajes');
  if (!data.ok) {
    el.innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  const mensajes = data.mensajes;
  if (mensajes.length === 0) {
    el.innerHTML = '<div class="empty">Ningún mensaje todavía.</div>';
    return;
  }

  el.innerHTML = mensajes.map((m) => _cardMensaje(m)).join('');

  mensajes.forEach((m) => {
    if (m.estado !== 'borrador_generado' && m.estado !== 'error_clasificacion') return;
    const card = document.getElementById(`mensaje-${m.id}`);
    const btnAprobar = card.querySelector('.btn-aprobar');
    const btnRechazar = card.querySelector('.btn-rechazar');
    const textarea = card.querySelector('.texto-respuesta');

    if (btnAprobar) {
      btnAprobar.addEventListener('click', async () => {
        const texto_final = textarea.value.trim();
        // v0.2.5 -- Impeccable P1: era la única acción que mandaba algo hacia
        // afuera (un mensaje real a un paciente) sin ningún paso de
        // confirmación, mientras acciones de menor riesgo (aprobar una
        // tarea, revocar un acceso) sí la tenían -- se pareja acá.
        const ok = await showConfirm(
          'Enviar esta respuesta',
          `Se va a mandar por WhatsApp a ${escHtml(m.telefono)}:<br><br>"${escHtml(texto_final)}"`,
          { confirmLabel: 'Enviar' },
        );
        if (!ok) return;
        btnAprobar.disabled = true;
        btnAprobar.textContent = 'Enviando...';
        const result = await mensajesWhatsappFetch(`/api/v1/whatsapp/mensajes/${m.id}/aprobar`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ texto_final }),
        });
        if (!result.ok) {
          showToast(result.error, 'error');
          btnAprobar.disabled = false;
          btnAprobar.textContent = 'Aprobar y enviar';
          return;
        }
        showToast('Respuesta enviada.', 'success');
        await _renderListaMensajesWhatsapp();
      });
    }

    btnRechazar.addEventListener('click', async () => {
      // v0.2.5 -- Impeccable P2 (re-critique): mismo fix que Operativa -- ver
      // esa vista para el detalle.
      // v0.2.6 -- si accion_drapp está seteado, el turno ya se creó/canceló
      // de verdad en DrApp -- rechazar esto NO lo deshace, solo evita que
      // el paciente se entere por WhatsApp. Aviso explícito para no confundir.
      const avisoTexto = m.accion_drapp
        ? `El turno ya ${m.accion_drapp === 'turno_creado' ? 'se creó' : 'se canceló'} en DrApp -- rechazar esto NO lo deshace, solo evita que el paciente reciba este mensaje.`
        : 'El borrador se descarta, no se manda nada.';
      const ok = await showConfirm('Rechazar este mensaje', avisoTexto, { confirmLabel: 'Rechazar', danger: true });
      if (!ok) return;
      btnRechazar.disabled = true;
      const result = await mensajesWhatsappFetch(`/api/v1/whatsapp/mensajes/${m.id}/rechazar`, { method: 'POST' });
      if (!result.ok) { showToast(result.error, 'error'); btnRechazar.disabled = false; return; }
      showToast('Mensaje descartado, no se mandó nada.', 'default');
      await _renderListaMensajesWhatsapp();
    });
  });
}

function _cardMensaje(m) {
  const tag = ESTADO_MENSAJE_TAG[m.estado] || { clase: '', label: m.estado };
  const accionable = m.estado === 'borrador_generado' || m.estado === 'error_clasificacion';
  const bloqueadoParaMi = m.requiere_profesional && mensajesWhatsappRol !== 'director';

  return `
    <div class="card" id="mensaje-${m.id}" style="margin-bottom:var(--space-3);">
      <div class="row-between">
        <div>
          <div style="font-weight:600;">${escHtml(m.telefono)}</div>
          <div style="font-size:12px; color:var(--muted);">${new Date(m.recibido_at).toLocaleString('es-AR')}</div>
        </div>
        <div class="row-wrap">
          ${m.urgente ? '<span class="tag nuevo">Urgente</span>' : ''}
          ${m.clasificacion ? `<span class="tag proceso">${CLASIFICACION_LABEL[m.clasificacion] || m.clasificacion}</span>` : ''}
          ${m.requiere_profesional ? '<span class="tag nuevo">Requiere profesional</span>' : ''}
          ${m.accion_drapp ? `<span class="tag resuelto">${ACCION_DRAPP_LABEL[m.accion_drapp] || m.accion_drapp}</span>` : ''}
          <span class="tag ${tag.clase}">${tag.label}</span>
        </div>
      </div>

      <p style="margin-top:var(--space-3); padding:10px; background:var(--bg-subtle); border-radius:6px;">
        "${escHtml(m.texto_original)}"
      </p>

      ${m.estado === 'error_clasificacion' ? `
        <div class="error-box" style="margin-top:var(--space-2);">
          La IA no pudo armar un borrador (${escHtml(m.error_clasificacion || 'error desconocido')}) — escribí la
          respuesta a mano abajo.
        </div>
      ` : ''}

      ${accionable ? `
        ${detectarPrecioOCobertura(m.borrador_respuesta) ? `
          <div class="error-box" style="margin-top:var(--space-2);">
            Este borrador menciona un precio o una cobertura de obra social — confirmá que sea
            un dato real antes de aprobar, la IA no tiene acceso a esa información.
          </div>
        ` : ''}
        <label style="margin-top:var(--space-3);">${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}</label>
        <textarea class="texto-respuesta" rows="3" aria-label="${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}" ${bloqueadoParaMi ? 'disabled' : ''}>${escHtml(m.borrador_respuesta || '')}</textarea>
        ${bloqueadoParaMi ? '<p class="help-text" style="color:var(--red);">Este mensaje toca algo clínico — solo el Director puede aprobarlo.</p>' : ''}
        <div class="row-wrap" style="margin-top:var(--space-2);">
          ${bloqueadoParaMi ? '' : '<button class="primary btn-aprobar">Aprobar y enviar</button>'}
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
