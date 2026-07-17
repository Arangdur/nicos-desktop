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
      <table>
        <tr><td>Dispositivo</td><td>${current.PAIRED_DEVICE_NAME || '—'}</td></tr>
        <tr><td>Mac</td><td>${current.MAC_LAN_HOST || '—'}:${current.MAC_LAN_PORT || '—'}</td></tr>
        <tr><td>Tareas en espera de conexión</td><td>${outboxCount}</td></tr>
      </table>
      <button class="secondary" id="btn-forget-pairing" style="margin-top:12px;">Olvidar vinculación</button>
    </div>
    <p style="font-size:12px; color:var(--muted); margin-top:10px;">
      Esta PC nunca guarda claves de IA ni credenciales de Google — esas viven solo
      en la Mac de Nicolás. Esta pantalla no tiene ni puede tener esos campos.
    </p>
  `;

  containerEl.querySelector('#btn-forget-pairing').addEventListener('click', async () => {
    await window.nicos.operativaForgetPairing();
    await window.nicos.setRole('operativa'); // fuerza a re-bootear -> vuelve a pairing.html
  });
}
