// Componente reutilizable de Ajustes, montado tanto en la vista Director como en la Operativa.
// Los campos de secretos siempre arrancan vacíos en el input (nunca se muestra el valor real,
// solo si "ya está configurado" o no) — al guardar, si el campo quedó vacío, no se toca lo
// que ya había guardado (permite editar un solo campo sin tener que re-pegar todos los demás).

async function renderSettingsPanel(containerEl) {
  const current = await window.nicos.getMaskedSettings();

  containerEl.innerHTML = `
    <div class="card">
      <h3>Ajustes — Rol de esta instalación</h3>
      <p style="font-size:13px; color:var(--muted);">Rol actual: <b>${current.role === 'director' ? 'Director (Nicolás)' : 'Operativa (Marianela)'}</b></p>
      <button class="secondary" id="btn-change-role">Cambiar rol</button>
    </div>

    <div class="card">
      <h3>Claves de IA</h3>
      <label>API key de Anthropic (Claude) ${current.ANTHROPIC_API_KEY_configurado ? '— ya configurada ✓' : '— falta configurar'}</label>
      <input type="password" id="anthropic-key" placeholder="sk-ant-...">
      <label>Modelo de Anthropic</label>
      <input type="text" id="anthropic-model" value="${current.ANTHROPIC_MODEL || 'claude-sonnet-5'}">

      <label style="margin-top:16px;">API key de OpenAI (ChatGPT) ${current.OPENAI_API_KEY_configurado ? '— ya configurada ✓' : '— falta configurar'}</label>
      <input type="password" id="openai-key" placeholder="sk-...">
      <label>Modelo de OpenAI</label>
      <input type="text" id="openai-model" value="${current.OPENAI_MODEL || 'gpt-5'}">
    </div>

    <div class="card">
      <h3>Google Sheets — Bot WhatsApp Consultorio</h3>
      <label>ID de la planilla (de la URL de Google Sheets)</label>
      <input type="text" id="sheet-id" value="${current.WHATSAPP_SHEET_ID || ''}" placeholder="1AbCdEfGh...">
      <label>Credenciales de la cuenta de servicio (JSON completo) ${current.GOOGLE_SERVICE_ACCOUNT_JSON_configurado ? '— ya configuradas ✓' : '— falta configurar'}</label>
      <textarea id="google-creds" rows="6" placeholder='{"type": "service_account", ...}'></textarea>
    </div>

    <div id="settings-status" style="font-size:13px; margin-bottom:10px;"></div>
    <button class="primary" id="btn-save-settings">Guardar</button>
  `;

  containerEl.querySelector('#btn-change-role').addEventListener('click', async () => {
    await window.nicos.setRole(null);
  });

  containerEl.querySelector('#btn-save-settings').addEventListener('click', async () => {
    const statusEl = containerEl.querySelector('#settings-status');
    statusEl.textContent = 'Guardando...';
    const update = {
      ANTHROPIC_MODEL: containerEl.querySelector('#anthropic-model').value.trim(),
      OPENAI_MODEL: containerEl.querySelector('#openai-model').value.trim(),
      WHATSAPP_SHEET_ID: containerEl.querySelector('#sheet-id').value.trim(),
    };
    const anthropicKey = containerEl.querySelector('#anthropic-key').value.trim();
    const openaiKey = containerEl.querySelector('#openai-key').value.trim();
    const googleCreds = containerEl.querySelector('#google-creds').value.trim();
    if (anthropicKey) update.ANTHROPIC_API_KEY = anthropicKey;
    if (openaiKey) update.OPENAI_API_KEY = openaiKey;
    if (googleCreds) update.GOOGLE_SERVICE_ACCOUNT_JSON = googleCreds;

    try {
      await window.nicos.saveSettings(update);
      statusEl.textContent = 'Guardado. El sidecar se reinició con la nueva configuración.';
      statusEl.style.color = 'var(--green)';
    } catch (e) {
      statusEl.textContent = 'Error al guardar: ' + e.message;
      statusEl.style.color = 'var(--red)';
    }
  });
}
