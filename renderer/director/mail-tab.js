// Pestaña "Bandeja de Mail" (vista Director) — v0.2.6. Mail entrante de las
// dos casillas (consultorio y Fundación Abate): la IA clasifica y arma un
// borrador, el Director aprueba (editado o tal cual) o rechaza. A diferencia
// de mensajes-whatsapp-tab.js, acá aprobar/rechazar es SIEMPRE Director-only
// -- ver sidecar/mail_entrante.py. Mismo estilo que esa pestaña.

let mailApiBase = null;
let mailRol = 'director';

const CASILLA_LABEL = { consultorio: 'Consultorio', abate: 'Fundación Abate' };
const CATEGORIA_MAIL_LABEL = {
  turno: 'Turno', administrativo: 'Administrativo', medico: 'Médico', queja: 'Queja', spam: 'Spam', otro: 'Otro',
};
const ESTADO_MAIL_TAG = {
  recibido: { clase: 'proceso', label: 'Procesando...' },
  borrador_generado: { clase: 'proceso', label: 'Borrador listo' },
  error_clasificacion: { clase: 'nuevo', label: 'Sin borrador (falló la IA)' },
  aprobado_enviado: { clase: 'resuelto', label: 'Enviado' },
  rechazado: { clase: '', label: 'Rechazado' },
};

function initMailTab(apiBase, rol) {
  mailApiBase = apiBase;
  mailRol = rol || 'director';
}

async function mailFetch(path, opts) {
  const res = await fetch(`${mailApiBase}${path}`, opts);
  return res.json();
}

async function loadMailTab() {
  const el = document.getElementById('tab-mail');
  el.innerHTML = `
    <div class="card">
      <h3>Bandeja de Mail</h3>
      <p class="help-text">
        Cada mail que llega a novogen.salud@gmail.com o fundacion.abate@gmail.com pasa primero
        por acá — la IA arma un borrador de respuesta, lo revisás (editable) y recién ahí se
        manda. Aprobar o rechazar es siempre del Director, sin excepción.
      </p>
    </div>
    <div id="mail-lista"></div>
  `;
  await _renderListaMail();
}

async function _renderListaMail() {
  const el = document.getElementById('mail-lista');
  el.innerHTML = '<div class="empty">Cargando...</div>';
  const data = await mailFetch('/api/v1/mail');
  if (!data.ok) {
    el.innerHTML = `<div class="error-box">${escHtml(data.error)}</div>`;
    return;
  }
  const mails = data.mail;
  if (mails.length === 0) {
    el.innerHTML = '<div class="empty">Ningún mail todavía.</div>';
    return;
  }

  el.innerHTML = mails.map((m) => _cardMail(m)).join('');

  mails.forEach((m) => {
    if (m.estado !== 'borrador_generado' && m.estado !== 'error_clasificacion') return;
    if (mailRol !== 'director') return; // Operativa solo ve, no puede accionar -- ver mail_entrante.py
    const card = document.getElementById(`mail-${m.id}`);
    const btnAprobar = card.querySelector('.btn-aprobar');
    const btnRechazar = card.querySelector('.btn-rechazar');
    const textarea = card.querySelector('.texto-respuesta');

    btnAprobar.addEventListener('click', async () => {
      const texto_final = textarea.value.trim();
      const ok = await showConfirm(
        'Enviar esta respuesta',
        `Se va a mandar por mail a ${escHtml(m.remitente)}:<br><br>"${escHtml(texto_final)}"`,
        { confirmLabel: 'Enviar' },
      );
      if (!ok) return;
      btnAprobar.disabled = true;
      btnAprobar.textContent = 'Enviando...';
      const result = await mailFetch(`/api/v1/mail/${m.id}/aprobar`, {
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
      await _renderListaMail();
    });

    btnRechazar.addEventListener('click', async () => {
      const ok = await showConfirm('Rechazar este mail', 'El borrador se descarta, no se manda nada.', { confirmLabel: 'Rechazar', danger: true });
      if (!ok) return;
      btnRechazar.disabled = true;
      const result = await mailFetch(`/api/v1/mail/${m.id}/rechazar`, { method: 'POST' });
      if (!result.ok) { showToast(result.error, 'error'); btnRechazar.disabled = false; return; }
      showToast('Mail descartado, no se mandó nada.', 'default');
      await _renderListaMail();
    });
  });
}

function _cardMail(m) {
  const tag = ESTADO_MAIL_TAG[m.estado] || { clase: '', label: m.estado };
  const accionable = (m.estado === 'borrador_generado' || m.estado === 'error_clasificacion') && m.categoria !== 'spam';
  const bloqueadoParaMi = mailRol !== 'director';

  return `
    <div class="card" id="mail-${m.id}" style="margin-bottom:var(--space-3);">
      <div class="row-between">
        <div>
          <div style="font-weight:600;">${escHtml(m.asunto || '(sin asunto)')}</div>
          <div style="font-size:12px; color:var(--muted);">${escHtml(m.remitente)} — ${new Date(m.recibido_at + 'Z').toLocaleString('es-AR')}</div>
        </div>
        <div class="row-wrap">
          <span class="tag">${CASILLA_LABEL[m.casilla] || m.casilla}</span>
          ${m.categoria ? `<span class="tag proceso">${CATEGORIA_MAIL_LABEL[m.categoria] || m.categoria}</span>` : ''}
          <span class="tag ${tag.clase}">${tag.label}</span>
        </div>
      </div>

      <p style="margin-top:var(--space-3); padding:10px; background:var(--bg-subtle); border-radius:6px; white-space:pre-wrap;">
        ${escHtml(m.cuerpo_original)}
      </p>

      ${m.estado === 'error_clasificacion' ? `
        <div class="error-box" style="margin-top:var(--space-2);">
          La IA no pudo armar un borrador (${escHtml(m.error_detalle || 'error desconocido')}) — escribí la
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
        <textarea class="texto-respuesta" rows="4" aria-label="${m.estado === 'error_clasificacion' ? 'Tu respuesta' : 'Borrador (editable)'}" ${bloqueadoParaMi ? 'disabled' : ''}>${escHtml(m.borrador_respuesta || '')}</textarea>
        ${bloqueadoParaMi ? '<p class="help-text" style="color:var(--red);">Aprobar o rechazar mail es siempre del Director.</p>' : ''}
        <div class="row-wrap" style="margin-top:var(--space-2);">
          ${bloqueadoParaMi ? '' : '<button class="primary btn-aprobar">Aprobar y enviar</button><button class="secondary btn-rechazar">Rechazar</button>'}
        </div>
      ` : m.estado === 'aprobado_enviado' ? `
        <p style="margin-top:var(--space-2); font-size:13px; color:var(--muted);">
          Se mandó: "${escHtml(m.respuesta_final)}"
        </p>
      ` : m.categoria === 'spam' && m.estado === 'borrador_generado' ? `
        <p style="margin-top:var(--space-2); font-size:13px; color:var(--muted);">Clasificado como spam -- no necesita respuesta.</p>
      ` : ''}
    </div>
  `;
}
