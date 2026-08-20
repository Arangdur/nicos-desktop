// Panel de Ajustes de la vista DIRECTOR — el único lugar de todo el sistema donde
// se cargan API keys de IA, Twilio y DrApp. La vista Operativa usa
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

      <label>API key de OpenAI (ChatGPT) ${current.OPENAI_API_KEY_configurado ? '— ya configurada ✓' : '— falta configurar'}</label>
      <input type="password" id="openai-key" placeholder="sk-...">
      <label>Modelo de OpenAI</label>
      <input type="text" id="openai-model" value="${current.OPENAI_MODEL || 'gpt-5'}">
    </div>

    <div class="card">
      <h3>Red — Tailscale</h3>
      <p class="help-text">
        La PC de Marianela se conecta a través de Tailscale, nunca por la red WiFi de la
        oficina en claro. Necesitás tener Tailscale instalado y logueado en esta Mac
        (<code>brew install tailscale && sudo tailscale up</code>), y después pegar acá
        la IP que te da <code>tailscale ip -4</code>.
      </p>
      <label>IP de Tailscale de esta Mac (empieza con 100.)</label>
      <input type="text" id="tailscale-ip" value="${current.TAILSCALE_IP || ''}" placeholder="100.x.y.z">
    </div>

    <div class="card">
      <h3>WhatsApp — Twilio (recordatorio de turnos)</h3>
      <p class="help-text">
        Se usa para mandar el recordatorio de turnos de Medicina General ~24hs antes
        (Psiquiatría ya tiene su propio recordatorio en DrApp). Los datos están en tu
        cuenta de Twilio, sección Account Info.
      </p>
      <label>Account SID</label>
      <input type="text" id="twilio-sid" value="${current.TWILIO_ACCOUNT_SID || ''}" placeholder="ACxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx">
      <label>Auth Token ${current.TWILIO_AUTH_TOKEN_configurado ? '— ya configurado ✓' : '— falta configurar'}</label>
      <input type="password" id="twilio-token" placeholder="••••••••••••••••">
      <label>Número de WhatsApp de Twilio (con whatsapp:+, se agrega solo)</label>
      <input type="text" id="twilio-from" value="${current.TWILIO_WHATSAPP_FROM || ''}" placeholder="+14155238886">
    </div>

    <div class="card">
      <h3>WhatsApp — Mensajes entrantes (Bandeja de borradores)</h3>
      <p class="help-text">
        Para que NicOS pueda RECIBIR WhatsApp de pacientes (no solo mandar recordatorios)
        hace falta exponer una única ruta a internet a través de Tailscale Funnel — nunca
        el resto de la app. Pegá acá la URL pública exacta que te da el Funnel (sin
        "/whatsapp/inbound" al final, se agrega solo) y configurá esa misma URL +
        "/whatsapp/inbound" como webhook "WHEN A MESSAGE COMES IN" en la consola de Twilio.
        Sin este campo, la bandeja de mensajes entrantes queda inactiva sin romper nada más.
      </p>
      <label>URL pública del Funnel (ej. https://tu-mac.tailXXXX.ts.net)</label>
      <input type="text" id="twilio-webhook-base" value="${current.TWILIO_WEBHOOK_BASE_URL || ''}" placeholder="https://macbook-de-nicolas.tailXXXX.ts.net">
      <label>WhatsApp del consultorio (Marianela) -- para reenviarle pedidos de receta</label>
      <input type="text" id="consultorio-whatsapp" value="${current.CONSULTORIO_WHATSAPP_NUMERO || ''}" placeholder="+5493537599192">
      <p class="help-text">
        Cuando llega un pedido de receta, además de contestarle al paciente se le reenvía
        el pedido a este número para que Marianela se contacte y siga el trámite. Vacío = no
        se reenvía nada (la receta sigue viéndose en la Bandeja igual).
      </p>
    </div>

    <div class="card">
      <h3>Facturación por WhatsApp — quién puede pedir una factura</h3>
      <p class="help-text">
        El número de WhatsApp del consultorio es el mismo al que te escriben los pacientes —
        sin esto, cualquiera que mande "facturale..." generaría un borrador de factura real.
        Poné tu propio número (formato +549..., el mismo con el que hablás por WhatsApp),
        separados por coma si usás más de uno. Vacío = nadie puede pedir facturas por WhatsApp.
      </p>
      <label>Tu(s) número(s) de WhatsApp autorizados</label>
      <input type="text" id="facturacion-telefonos" value="${current.FACTURACION_TELEFONOS_AUTORIZADOS || ''}" placeholder="+5493513334455">
    </div>

    <div class="card">
      <h3>DrApp — Agenda real del consultorio</h3>
      <p class="help-text">
        Para que NicOS lea y gestione turnos reales (disponibilidad, alta, cancelación).
        La API key se genera en developers.drapp.com.ar (registrando una app) o en el
        backoffice de DrApp. El Team ID es el código que figura arriba a la derecha en
        DrApp Web, o en "Mi cuenta" de la app.
      </p>
      <label>API key ${current.DRAPP_API_KEY_configurado ? '— ya configurada ✓' : '— falta configurar'}</label>
      <input type="password" id="drapp-key" placeholder="drapp_sandbox_... o drapp_live_...">
      <label>Team ID</label>
      <input type="text" id="drapp-team" value="${current.DRAPP_TEAM_ID || ''}" placeholder="clinica-norte">
    </div>

    <div class="card">
      <h3>DrApp — Reserva de turnos por WhatsApp (Fase C)</h3>
      <p class="help-text">
        Para que el bot pueda ofrecer horarios reales y crear/cancelar el turno solo cuando
        el paciente confirma por escrito. El Resource ID sos vos como profesional dentro de tu
        cuenta de DrApp (ej. <code>resources/8c8a2304</code>) y el Service Key identifica
        "Medicina General / Consulta" (ej. <code>pms_specialties:medicina-general/pms_practices:consulta</code>)
        -- los dos se ven en la respuesta de <code>GET /teams/&lt;team&gt;/resources</code> y
        <code>/services</code> de la API de DrApp. Sin estos dos campos, el bot sigue
        respondiendo "alguien te va a confirmar" como hasta ahora -- no rompe nada, solo no
        ofrece horarios reales todavía.
      </p>
      <label>Resource ID</label>
      <input type="text" id="drapp-resource-id" value="${current.DRAPP_RESOURCE_ID || ''}" placeholder="resources/8c8a2304">
      <label>Service Key — Medicina General</label>
      <input type="text" id="drapp-service-key" value="${current.DRAPP_SERVICE_KEY_MEDICINA_GENERAL || ''}" placeholder="pms_specialties:medicina-general/pms_practices:consulta">
    </div>

    <div class="card">
      <h3>Mail entrante — Gmail (consultorio y Fundación Abate)</h3>
      <p class="help-text">
        Para que NicOS lea y conteste (con tu aprobación) los mails de novogen.salud@gmail.com
        y fundacion.abate@gmail.com. El Client ID/Secret se generan UNA sola vez en Google Cloud
        Console (credenciales OAuth tipo "Desktop app") y sirven para las dos casillas. Cada
        refresh token se obtiene corriendo <code>python3 sidecar/gmail_oauth_setup.py &lt;casilla&gt;</code>
        vos mismo, una vez por casilla (necesita tu login real en el navegador) — ver el
        comentario de setup en sidecar/gmail_client.py para los pasos completos.
      </p>
      <label>Client ID</label>
      <input type="text" id="gmail-client-id" value="${current.GMAIL_CLIENT_ID || ''}" placeholder="xxxx.apps.googleusercontent.com">
      <label>Client Secret ${current.GMAIL_CLIENT_SECRET_configurado ? '— ya configurado ✓' : '— falta configurar'}</label>
      <input type="password" id="gmail-client-secret" placeholder="••••••••••••••••">
      <label>Refresh token — consultorio (novogen.salud@gmail.com) ${current.GMAIL_REFRESH_TOKEN_CONSULTORIO_configurado ? '— ya configurado ✓' : '— falta configurar'}</label>
      <input type="password" id="gmail-refresh-consultorio" placeholder="••••••••••••••••">
      <label>Refresh token — Fundación Abate (fundacion.abate@gmail.com) ${current.GMAIL_REFRESH_TOKEN_ABATE_configurado ? '— ya configurado ✓' : '— falta configurar'}</label>
      <input type="password" id="gmail-refresh-abate" placeholder="••••••••••••••••">
    </div>

    <div class="row" style="margin-bottom:var(--space-4);">
      <button class="primary" id="btn-save-settings">Guardar</button>
      <span id="settings-status" style="font-size:var(--text-sm);"></span>
    </div>

    <div class="card">
      <h3>Personas con acceso a NicOS</h3>
      <p class="help-text">
        Cada persona vinculada tiene un rol fijo asignado en este paso — no se puede
        cambiar después sin revocar y volver a vincular. Nunca tienen tus claves de IA
        ni de Google, solo un token revocable.
      </p>

      <label style="margin-top:var(--space-3);">Nombre completo</label>
      <input type="text" id="nueva-persona-nombre" placeholder="Ej. Daniela Fernández">

      <label style="margin-top:var(--space-3);">Rol</label>
      <select id="nueva-persona-rol">
        <option value="operativa">Operativa (Secretaria)</option>
        <option value="enfermero">Enfermero/a de Abate</option>
      </select>

      <div id="campo-nuevo-turno" style="margin-top:var(--space-3); display:none;">
        <label>Turno habitual (opcional, informativo)</label>
        <select id="nueva-persona-turno">
          <option value="">Sin especificar</option>
          <option value="manana">Mañana</option>
          <option value="tarde">Tarde</option>
          <option value="noche">Noche</option>
          <option value="rotativo">Rotativo</option>
        </select>
      </div>

      <button class="secondary" id="btn-start-pairing" style="margin-top:var(--space-4);">Generar código de vinculación</button>
      <div id="pairing-code-display" style="margin:var(--space-3) 0; font-size:var(--text-base);"></div>
      <div id="devices-list" style="margin-top:var(--space-3);"></div>
    </div>
  `;

  containerEl.querySelector('#nueva-persona-rol').addEventListener('change', (e) => {
    containerEl.querySelector('#campo-nuevo-turno').style.display = e.target.value === 'enfermero' ? 'block' : 'none';
  });

  containerEl.querySelector('#btn-save-settings').addEventListener('click', async () => {
    const statusEl = containerEl.querySelector('#settings-status');
    statusEl.textContent = 'Guardando...';
    const update = {
      ANTHROPIC_MODEL: containerEl.querySelector('#anthropic-model').value.trim(),
      OPENAI_MODEL: containerEl.querySelector('#openai-model').value.trim(),
      TAILSCALE_IP: containerEl.querySelector('#tailscale-ip').value.trim(),
      TWILIO_ACCOUNT_SID: containerEl.querySelector('#twilio-sid').value.trim(),
      TWILIO_WHATSAPP_FROM: containerEl.querySelector('#twilio-from').value.trim(),
      DRAPP_TEAM_ID: containerEl.querySelector('#drapp-team').value.trim(),
      DRAPP_RESOURCE_ID: containerEl.querySelector('#drapp-resource-id').value.trim(),
      DRAPP_SERVICE_KEY_MEDICINA_GENERAL: containerEl.querySelector('#drapp-service-key').value.trim(),
      TWILIO_WEBHOOK_BASE_URL: containerEl.querySelector('#twilio-webhook-base').value.trim().replace(/\/$/, ''),
      CONSULTORIO_WHATSAPP_NUMERO: containerEl.querySelector('#consultorio-whatsapp').value.trim(),
      FACTURACION_TELEFONOS_AUTORIZADOS: containerEl.querySelector('#facturacion-telefonos').value.trim(),
      GMAIL_CLIENT_ID: containerEl.querySelector('#gmail-client-id').value.trim(),
    };
    const anthropicKey = containerEl.querySelector('#anthropic-key').value.trim();
    const openaiKey = containerEl.querySelector('#openai-key').value.trim();
    const twilioToken = containerEl.querySelector('#twilio-token').value.trim();
    const drappKey = containerEl.querySelector('#drapp-key').value.trim();
    const gmailClientSecret = containerEl.querySelector('#gmail-client-secret').value.trim();
    const gmailRefreshConsultorio = containerEl.querySelector('#gmail-refresh-consultorio').value.trim();
    const gmailRefreshAbate = containerEl.querySelector('#gmail-refresh-abate').value.trim();
    if (anthropicKey) update.ANTHROPIC_API_KEY = anthropicKey;
    if (openaiKey) update.OPENAI_API_KEY = openaiKey;
    if (drappKey) update.DRAPP_API_KEY = drappKey;
    if (twilioToken) update.TWILIO_AUTH_TOKEN = twilioToken;
    if (gmailClientSecret) update.GMAIL_CLIENT_SECRET = gmailClientSecret;
    if (gmailRefreshConsultorio) update.GMAIL_REFRESH_TOKEN_CONSULTORIO = gmailRefreshConsultorio;
    if (gmailRefreshAbate) update.GMAIL_REFRESH_TOKEN_ABATE = gmailRefreshAbate;

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

  const ROL_LABEL = { operativa: 'Operativa (Secretaria)', enfermero: 'Enfermero/a de Abate' };
  const TURNO_LABEL = { manana: 'mañana', tarde: 'tarde', noche: 'noche', rotativo: 'rotativo' };

  async function loadDevices() {
    const listEl = containerEl.querySelector('#devices-list');
    const res = await fetch(`${apiBase}/api/v1/users`);
    const data = await res.json();
    if (!data.ok) {
      listEl.innerHTML = `<div class="error-box">${data.error}</div>`;
      return;
    }
    if (data.users.length === 0 && data.pending.length === 0) {
      listEl.innerHTML = '<div class="empty">Nadie vinculado todavía.</div>';
      return;
    }
    const filasPendientes = data.pending.map((p) => `
      <tr>
        <td>${p.assigned_display_name || '(sin nombre)'}<div class="rol-sub" style="font-size:12px; color:var(--muted);">Datos personales pendientes -- los completa la persona al hacer su alta</div></td>
        <td>${ROL_LABEL[p.assigned_role] || p.assigned_role}${p.assigned_turno ? ' · turno ' + (TURNO_LABEL[p.assigned_turno] || p.assigned_turno) : ''}</td>
        <td>—</td>
        <td><span class="tag proceso">Código generado</span></td>
        <td><button class="secondary btn-cancel-code" data-code="${p.code}">Cancelar código</button></td>
      </tr>
    `).join('');
    const filasPersonas = data.users.map((u) => `
      <tr>
        <td>${u.display_name}${u.dni ? `<div style="font-size:12px; color:var(--muted);">DNI ${u.dni}</div>` : ''}</td>
        <td>${ROL_LABEL[u.role] || u.role}${u.turno ? ' · turno ' + (TURNO_LABEL[u.turno] || u.turno) : ''}</td>
        <td>${u.created_at ? new Date(u.created_at).toLocaleString('es-AR') : '—'}</td>
        <td>${u.revoked_at ? '<span class="tag nuevo">Revocado</span>' : '<span class="tag resuelto">Activo</span>'}</td>
        <td>${u.revoked_at ? '' : `<button class="secondary btn-revoke" data-id="${u.user_id}">Revocar</button>`}</td>
      </tr>
    `).join('');
    listEl.innerHTML = `
      <table>
        <tr><th>Persona</th><th>Rol</th><th>Vinculado</th><th>Estado</th><th></th></tr>
        ${filasPersonas}${filasPendientes}
      </table>
    `;
    listEl.querySelectorAll('.btn-revoke').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const ok = await showConfirm('Revocar acceso', 'Esta persona pierde el acceso a NicOS de inmediato -- va a necesitar un código nuevo para volver a vincularse.', { confirmLabel: 'Revocar', danger: true });
        if (!ok) return;
        await fetch(`${apiBase}/api/v1/users/${btn.dataset.id}/revoke`, { method: 'POST' });
        showToast('Acceso revocado.');
        loadDevices();
      });
    });
    listEl.querySelectorAll('.btn-cancel-code').forEach((btn) => {
      btn.addEventListener('click', async () => {
        await fetch(`${apiBase}/api/v1/pairing/${btn.dataset.code}/cancel`, { method: 'POST' });
        showToast('Código cancelado.');
        loadDevices();
      });
    });
  }

  containerEl.querySelector('#btn-start-pairing').addEventListener('click', async () => {
    const displayName = containerEl.querySelector('#nueva-persona-nombre').value.trim();
    const role = containerEl.querySelector('#nueva-persona-rol').value;
    const turno = containerEl.querySelector('#nueva-persona-turno').value;
    const el = containerEl.querySelector('#pairing-code-display');
    if (!displayName) {
      el.innerHTML = `<span style="color:var(--red);">Falta el nombre.</span>`;
      return;
    }
    const res = await fetch(`${apiBase}/api/v1/pairing/start`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ role, turno: turno || null, display_name: displayName }),
    });
    const data = await res.json();
    if (data.ok) {
      // v0.2.5 -- Impeccable P0: antes esto era solo el código de 6 dígitos,
      // y la persona nueva tenía que ADEMÁS escribir a mano la IP de
      // Tailscale de esta Mac en un campo aparte -- pedirle eso a alguien
      // sin perfil técnico (ver PRODUCT.md) era el hallazgo más grave del
      // audit de diseño. Ahora se arma un único código combinado
      // (código@IP) que ${displayName} pega en un solo campo -- ella nunca
      // tiene que saber qué es una IP ni de dónde sacarla.
      const ip = current.TAILSCALE_IP || '';
      const combinado = ip ? `${data.code}@${ip}` : data.code;
      el.innerHTML = `
        <div>Código para ${escHtml(displayName)} — válido 5 minutos:</div>
        <div class="row-wrap" style="margin-top:6px; align-items:center;">
          <b style="font-size:18px; letter-spacing:1px; font-family:monospace;">${escHtml(combinado)}</b>
          <button class="secondary" id="btn-copiar-codigo" style="padding:4px 10px; font-size:12px;">Copiar</button>
        </div>
        <p class="help-text" style="margin-top:4px;">
          Mandaselo tal cual (por WhatsApp, mensaje, o en persona) — lo pega entero en el único
          campo "Código de vinculación" de su pantalla, no hace falta que escriba nada más.
        </p>
      `;
      el.querySelector('#btn-copiar-codigo').addEventListener('click', async () => {
        await navigator.clipboard.writeText(combinado);
        showToast('Copiado.');
      });
      loadDevices();
    } else {
      el.innerHTML = `<span style="color:var(--red);">${data.error}</span>`;
    }
  });

  loadDevices();
}
