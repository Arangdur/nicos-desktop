// Panel de Ajustes de la vista DIRECTOR — el único lugar de todo el sistema donde
// se cargan API keys de IA y credenciales de Google. La vista Operativa usa
// settings-panel-operativa.js, que NO tiene ninguno de estos campos.
//
// `apiBase` es la URL del sidecar LOCAL (127.0.0.1:puerto) — se usa para pairing
// y lista de dispositivos vinculados, que son operaciones de solo-Director.

async function renderSettingsPanelDirector(containerEl, apiBase, onPortChange) {
  const current = await window.nicos.getMaskedSettings();

  containerEl.innerHTML = `
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
      <h3>Red — Tailscale</h3>
      <p style="font-size:13px; color:var(--muted);">
        La PC de Marianela se conecta a través de Tailscale, nunca por la red WiFi de la
        oficina en claro. Necesitás tener Tailscale instalado y logueado en esta Mac
        (<code>brew install tailscale && sudo tailscale up</code>), y después pegar acá
        la IP que te da <code>tailscale ip -4</code>.
      </p>
      <label>IP de Tailscale de esta Mac (empieza con 100.)</label>
      <input type="text" id="tailscale-ip" value="${current.TAILSCALE_IP || ''}" placeholder="100.x.y.z">
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

    <div class="card" style="margin-top:24px;">
      <h3>Dispositivos vinculados (PC de Marianela y otros)</h3>
      <p style="font-size:13px; color:var(--muted);">
        Estos dispositivos pueden enviar tareas por la red local — nunca tienen tus claves
        de IA ni de Google, solo un token revocable.
      </p>
      <button class="secondary" id="btn-start-pairing">Vincular nuevo dispositivo</button>
      <div id="pairing-code-display" style="margin:12px 0; font-size:14px;"></div>
      <div id="devices-list" style="margin-top:12px;"></div>
    </div>
  `;

  containerEl.querySelector('#btn-save-settings').addEventListener('click', async () => {
    const statusEl = containerEl.querySelector('#settings-status');
    statusEl.textContent = 'Guardando...';
    const update = {
      ANTHROPIC_MODEL: containerEl.querySelector('#anthropic-model').value.trim(),
      OPENAI_MODEL: containerEl.querySelector('#openai-model').value.trim(),
      WHATSAPP_SHEET_ID: containerEl.querySelector('#sheet-id').value.trim(),
      TAILSCALE_IP: containerEl.querySelector('#tailscale-ip').value.trim(),
    };
    const anthropicKey = containerEl.querySelector('#anthropic-key').value.trim();
    const openaiKey = containerEl.querySelector('#openai-key').value.trim();
    const googleCreds = containerEl.querySelector('#google-creds').value.trim();
    if (anthropicKey) update.ANTHROPIC_API_KEY = anthropicKey;
    if (openaiKey) update.OPENAI_API_KEY = openaiKey;
    if (googleCreds) update.GOOGLE_SERVICE_ACCOUNT_JSON = googleCreds;

    try {
      const result = await window.nicos.saveSettings(update);
      if (result && result.port) {
        // El sidecar se reinició en un puerto nuevo (efímero) -- si no
        // actualizamos apiBase acá, todo lo que sigue usando este panel
        // (vincular dispositivo, listar/revocar) queda apuntando a un
        // proceso que ya no existe, sin ningún error visible (fetch a un
        // puerto cerrado). Como `apiBase` es el mismo parámetro que ya
        // capturaron los listeners de abajo, reasignarlo acá alcanza --
        // comparten el mismo binding, no una copia.
        apiBase = `http://127.0.0.1:${result.port}`;
        if (onPortChange) onPortChange(result.port);
      }
      statusEl.textContent = 'Guardado. El sidecar se reinició con la nueva configuración.';
      statusEl.style.color = 'var(--green)';
      loadDevices();
    } catch (e) {
      statusEl.textContent = 'Error al guardar: ' + e.message;
      statusEl.style.color = 'var(--red)';
    }
  });

  async function loadDevices() {
    const listEl = containerEl.querySelector('#devices-list');
    const res = await fetch(`${apiBase}/api/v1/devices`);
    const data = await res.json();
    if (!data.ok) {
      listEl.innerHTML = `<div class="error-box">${data.error}</div>`;
      return;
    }
    if (data.devices.length === 0) {
      listEl.innerHTML = '<div class="empty">Ningún dispositivo vinculado todavía.</div>';
      return;
    }
    listEl.innerHTML = `
      <table>
        <tr><th>Dispositivo</th><th>Vinculado</th><th>Estado</th><th></th></tr>
        ${data.devices.map((d) => `
          <tr>
            <td>${d.device_name}</td>
            <td>${new Date(d.paired_at).toLocaleString('es-AR')}</td>
            <td>${d.revoked_at ? '<span class="tag nuevo">Revocado</span>' : '<span class="tag resuelto">Activo</span>'}</td>
            <td>${d.revoked_at ? '' : `<button class="secondary btn-revoke" data-id="${d.device_id}">Revocar</button>`}</td>
          </tr>
        `).join('')}
      </table>
    `;
    listEl.querySelectorAll('.btn-revoke').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await fetch(`${apiBase}/api/v1/devices/${btn.dataset.id}/revoke`, { method: 'POST' });
        loadDevices();
      });
    });
  }

  containerEl.querySelector('#btn-start-pairing').addEventListener('click', async () => {
    const res = await fetch(`${apiBase}/api/v1/pairing/start`, { method: 'POST' });
    const data = await res.json();
    const el = containerEl.querySelector('#pairing-code-display');
    if (data.ok) {
      el.innerHTML = `Código: <b style="font-size:20px; letter-spacing:2px;">${data.code}</b> — válido 5 minutos. Ingresalo en la PC de Marianela.`;
    } else {
      el.innerHTML = `<span style="color:var(--red);">${data.error}</span>`;
    }
  });

  loadDevices();
}
