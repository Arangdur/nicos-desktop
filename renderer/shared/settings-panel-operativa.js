// Panel de Ajustes de la vista OPERATIVA. A propósito, este archivo NO tiene
// ningún campo de API key ni credencial de Google — Marianela no puede cargar
// esos datos ni por error, porque el formulario ni existe acá. Solo muestra
// con qué Mac está vinculada esta PC y permite "olvidar" la vinculación.

async function renderSettingsPanelOperativa(containerEl) {
  const current = await window.nicos.getMaskedSettings();
  const outboxCount = await window.nicos.operativaOutboxCount();

  containerEl.innerHTML = `
    <div class="card">
      <h3>Vinculación con la Mac</h3>
      <dl class="kv-grid">
        <dt>Dispositivo</dt><dd>${current.PAIRED_DEVICE_NAME || '—'}</dd>
        <dt>Mac</dt><dd>${current.MAC_LAN_HOST || '—'}:${current.MAC_LAN_PORT || '—'}</dd>
        <dt>Tareas en espera de conexión</dt><dd>${outboxCount}</dd>
      </dl>
      <button class="danger" id="btn-forget-pairing" style="margin-top:var(--space-4);">Olvidar vinculación</button>
    </div>
    <p class="help-text">
      Esta PC nunca guarda claves de IA ni credenciales de Google — esas viven solo
      en la Mac de Nicolás. Esta pantalla no tiene ni puede tener esos campos.
    </p>
  `;

  containerEl.querySelector('#btn-forget-pairing').addEventListener('click', async () => {
    const ok = await showConfirm('Olvidar vinculación', 'Esta PC deja de estar conectada a la Mac de Nicolás -- vas a tener que vincularla de nuevo con un código.', { confirmLabel: 'Olvidar', danger: true });
    if (!ok) return;
    await window.nicos.operativaForgetPairing();
    await window.nicos.setRole('operativa'); // fuerza a re-bootear -> vuelve a pairing.html
  });
}
