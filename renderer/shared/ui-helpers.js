// Reemplaza alert()/prompt()/confirm() nativos (feos, bloquean todo el
// proceso, no combinan con el resto de la interfaz) por un toast y un modal
// propios, con el mismo aspecto que el resto de la app.

function _toastRoot() {
  let root = document.getElementById('toast-root');
  if (!root) {
    root = document.createElement('div');
    root.id = 'toast-root';
    document.body.appendChild(root);
  }
  return root;
}

function showToast(message, type = 'default', durationMs = 4000) {
  const root = _toastRoot();
  const el = document.createElement('div');
  el.className = `toast${type !== 'default' ? ' ' + type : ''}`;
  el.textContent = message;
  root.appendChild(el);
  setTimeout(() => el.remove(), durationMs);
}

function _modal({ title, body, fields = [], confirmLabel = 'Confirmar', cancelLabel = 'Cancelar', danger = false }) {
  return new Promise((resolve) => {
    const backdrop = document.createElement('div');
    backdrop.className = 'modal-backdrop';
    const fieldsHtml = fields.map((f, i) => `
      <label>${f.label}</label>
      <textarea id="modal-field-${i}" rows="${f.rows || 2}" placeholder="${f.placeholder || ''}"></textarea>
    `).join('');
    // v0.2.5 -- Impeccable P1 (re-critique): el modal no tenía role="dialog"
    // ni aria-modal (un lector de pantalla no se enteraba de que se abrió
    // nada), no movía el foco adentro, no lo devolvía al cerrar, y no se
    // podía cancelar con Escape -- justo el modal que se usa para confirmar
    // el envío de WhatsApp, la acción más sensible de toda la app.
    backdrop.innerHTML = `
      <div class="modal-box" role="dialog" aria-modal="true" aria-labelledby="modal-title">
        <h4 id="modal-title">${title}</h4>
        ${body ? `<p>${body}</p>` : ''}
        ${fieldsHtml}
        <div class="modal-actions">
          <button class="secondary" id="modal-cancel">${cancelLabel}</button>
          <button class="${danger ? 'danger' : 'primary'}" id="modal-confirm">${confirmLabel}</button>
        </div>
      </div>
    `;
    const focoAnterior = document.activeElement;
    document.body.appendChild(backdrop);
    (backdrop.querySelector(`#modal-field-0`) || backdrop.querySelector('#modal-confirm')).focus();

    const cleanup = (result) => {
      backdrop.remove();
      document.removeEventListener('keydown', onKeydown);
      if (focoAnterior && focoAnterior.focus) focoAnterior.focus();
      resolve(result);
    };
    const onKeydown = (e) => { if (e.key === 'Escape') cleanup(null); };
    document.addEventListener('keydown', onKeydown);
    backdrop.querySelector('#modal-cancel').addEventListener('click', () => cleanup(null));
    backdrop.addEventListener('click', (e) => { if (e.target === backdrop) cleanup(null); });
    backdrop.querySelector('#modal-confirm').addEventListener('click', () => {
      const values = fields.map((_, i) => backdrop.querySelector(`#modal-field-${i}`).value.trim());
      cleanup(fields.length ? values : true);
    });
  });
}

function showConfirm(title, body, { confirmLabel = 'Confirmar', danger = false } = {}) {
  return _modal({ title, body, confirmLabel, danger }).then((r) => r === true);
}

function showPrompt(title, body, { placeholder = '', confirmLabel = 'Confirmar' } = {}) {
  return _modal({ title, body, fields: [{ label: '', placeholder, rows: 3 }], confirmLabel })
    .then((r) => (Array.isArray(r) ? r[0] : null));
}

// v0.2.6 -- heurística para frenar antes de aprobar un borrador de WhatsApp
// que afirma un precio o una cobertura de obra social (ver ai_router.py,
// regla 5 del prompt de mensajes: la IA no debería inventar esto, pero un
// borrador igual puede colarse con algo así, y quien aprueba puede no
// releerlo con cuidado -- esto es la señal visual, no un bloqueo real, para
// que salte a la vista antes de un clic apurado). Deliberadamente sobre-
// inclusiva: prefiere marcar de más a que se escape un precio inventado.
const _PRECIO_PATRON = /\$\s?\d|(?<!\d)\d{3,}\s*(pesos|ars)\b/i;
const _COBERTURA_PATRON = /obra\s+social|prepaga|cobertura|cubre|reintegr/i;

function detectarPrecioOCobertura(texto) {
  if (!texto) return false;
  return _PRECIO_PATRON.test(texto) || _COBERTURA_PATRON.test(texto);
}
