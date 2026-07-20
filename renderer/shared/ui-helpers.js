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
    backdrop.innerHTML = `
      <div class="modal-box">
        <h4>${title}</h4>
        ${body ? `<p>${body}</p>` : ''}
        ${fieldsHtml}
        <div class="modal-actions">
          <button class="secondary" id="modal-cancel">${cancelLabel}</button>
          <button class="${danger ? 'danger' : 'primary'}" id="modal-confirm">${confirmLabel}</button>
        </div>
      </div>
    `;
    document.body.appendChild(backdrop);

    const cleanup = (result) => {
      backdrop.remove();
      resolve(result);
    };
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
