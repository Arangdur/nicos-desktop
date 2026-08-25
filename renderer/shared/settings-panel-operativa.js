// Panel de Ajustes de las vistas no-Director (Operativa, Enfermero). A propósito,
// este archivo NO tiene ningún campo de API key ni credencial de Google — nadie
// puede cargar esos datos ni por error, porque el formulario ni existe acá.
// Solo muestra quién sos (la persona con sesión activa) y con qué Mac está
// vinculada esta PC, y permite "olvidar" tu propio acceso (no el de otras
// personas que puedan compartir el mismo dispositivo, ver login.html).

const UPDATE_STATUS_LABEL = {
  checking: 'Buscando actualizaciones...',
  available: 'Hay una versión nueva -- descargando...',
  'up-to-date': 'Ya tenés la última versión.',
  downloading: 'Descargando...',
  downloaded: 'Lista para instalar.',
  error: 'No se pudo buscar actualizaciones.',
};

async function renderSettingsPanelOperativa(containerEl) {
  const current = await window.nicos.getMaskedSettings();
  const identity = await window.nicos.identityActive();
  const outboxCount = await window.nicos.operativaOutboxCount();
  const about = await window.nicos.getAboutInfo();

  const TURNO_LABEL = { manana: 'mañana', tarde: 'tarde', noche: 'noche', rotativo: 'rotativo' };

  containerEl.innerHTML = `
    <div class="card">
      <h3>Tu vinculación con la Mac</h3>
      <dl class="kv-grid">
        <dt>Persona</dt><dd>${identity ? identity.display_name : '—'}</dd>
        ${identity && identity.turno ? `<dt>Turno</dt><dd>${TURNO_LABEL[identity.turno] || identity.turno}</dd>` : ''}
        <dt>Mac</dt><dd>${current.MAC_LAN_HOST || '—'}:${current.MAC_LAN_PORT || '—'}</dd>
        <dt>Tareas en espera de conexión</dt><dd>${outboxCount}</dd>
      </dl>
      <div class="row-wrap" style="margin-top:var(--space-4);">
        <button class="secondary" id="btn-switch-persona">Cambiar de persona</button>
        <button class="danger" id="btn-forget-identity">Olvidar mi acceso</button>
      </div>
    </div>

    <div class="card">
      <h3>Actualizaciones</h3>
      <dl class="kv-grid">
        <dt>Versión instalada</dt><dd>${about.app_version || '—'}</dd>
      </dl>
      <p class="help-text" id="update-status" style="margin-top:var(--space-2);">
        Se actualiza sola al abrir la app -- no hace falta hacer nada.
      </p>
      <div class="row-wrap" style="margin-top:var(--space-3);">
        <button class="secondary" id="btn-check-updates">Buscar actualizaciones ahora</button>
      </div>
    </div>

    <p class="help-text">
      Esta PC nunca guarda claves de IA ni credenciales de Google — esas viven solo
      en la Mac de Nicolás. Esta pantalla no tiene ni puede tener esos campos.
    </p>
  `;

  containerEl.querySelector('#btn-switch-persona').addEventListener('click', async () => {
    await window.nicos.identityLogout();
  });

  containerEl.querySelector('#btn-forget-identity').addEventListener('click', async () => {
    const ok = await showConfirm(
      'Olvidar mi acceso',
      'Vas a tener que pedirle a Nicolás un código nuevo para volver a vincularte -- esto no afecta a otras personas vinculadas en esta misma PC.',
      { confirmLabel: 'Olvidar', danger: true },
    );
    if (!ok || !identity) return;
    await window.nicos.identityForget(identity.user_id);
    await window.nicos.identityLogout();
  });

  const statusEl = containerEl.querySelector('#update-status');

  containerEl.querySelector('#btn-check-updates').addEventListener('click', async () => {
    const result = await window.nicos.checkForUpdates();
    if (!result.ok) {
      statusEl.textContent = result.error || 'No se pudo buscar actualizaciones (¿estás en modo desarrollo?).';
    }
  });

  // v0.2.9 -- la descarga y la instalación ya no las dispara este panel:
  // pasan solas en electron/auto-updater.js apenas se detecta una versión
  // nueva, sin depender de que esta pantalla esté abierta. Acá solo se
  // refleja el estado para quien esté mirando en ese momento.
  window.nicos.onUpdateStatus(({ status, version, percent, message }) => {
    if (status === 'available') {
      statusEl.textContent = `Versión ${version} disponible -- descargando sola...`;
    } else if (status === 'downloading') {
      statusEl.textContent = `Descargando actualización... ${percent}%`;
    } else if (status === 'downloaded') {
      statusEl.textContent = `Versión ${version} descargada -- instalando y reiniciando...`;
    } else if (status === 'error') {
      statusEl.textContent = `Error buscando actualizaciones: ${message}`;
    } else {
      statusEl.textContent = UPDATE_STATUS_LABEL[status] || '';
    }
  });
}
