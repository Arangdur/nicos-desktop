let personaEligiendo = null;

function mostrarPanel(nombre) {
  document.querySelectorAll('.panel').forEach((p) => p.classList.remove('open'));
  document.getElementById('panel-' + nombre).classList.add('open');
  document.querySelectorAll('.switch-btn').forEach((b) => b.classList.toggle('active', b.dataset.panel === nombre || (nombre === 'pin' && b.dataset.panel === 'login')));
}

async function renderListaLogin() {
  const identidades = await window.nicos.identityList();
  const el = document.getElementById('lista-personas-login');

  if (identidades.length === 0) {
    el.innerHTML = '<p class="help-text">Todavía no hay nadie vinculado en esta PC.</p>';
    return identidades;
  }

  const ROL_LABEL = { operativa: 'Operativa (Secretaria)', enfermero: 'Enfermero/a de Abate' };
  const TURNO_LABEL = { manana: 'mañana', tarde: 'tarde', noche: 'noche', rotativo: 'rotativo' };

  el.innerHTML = identidades.map((p) => `
    <div class="persona-pick" data-user-id="${p.user_id}">
      <div class="avatar">${(p.display_name || '?')[0].toUpperCase()}</div>
      <div>
        <div class="p-nombre">${p.display_name}</div>
        <div class="p-rol">${ROL_LABEL[p.role] || p.role}${p.turno ? ' · turno ' + (TURNO_LABEL[p.turno] || p.turno) : ''}</div>
      </div>
    </div>
  `).join('');

  el.querySelectorAll('.persona-pick').forEach((item) => {
    item.addEventListener('click', () => {
      const userId = item.dataset.userId;
      personaEligiendo = identidades.find((p) => p.user_id === userId);
      mostrarPanel('pin');
      document.getElementById('pin-nombre').textContent = `Hola, ${personaEligiendo.display_name}`;
      document.getElementById('pin-input').value = '';
      document.getElementById('pin-status').textContent = '';
      document.getElementById('pin-input').focus();
    });
  });

  return identidades;
}

async function init() {
  const identidades = await renderListaLogin();
  mostrarPanel(identidades.length > 0 ? 'login' : 'alta');

  // Si esta PC ya habla con una Mac (alguien más ya se vinculó acá), no hace
  // falta volver a pedir la IP -- se reusa la misma.
  const config = await window.nicos.getMaskedSettings();
  if (config.MAC_LAN_HOST) {
    document.getElementById('campo-ip').style.display = 'none';
  }
}

document.querySelectorAll('.switch-btn').forEach((btn) => {
  btn.addEventListener('click', () => mostrarPanel(btn.dataset.panel));
});

document.getElementById('link-otra-persona').addEventListener('click', () => {
  mostrarPanel('alta');
});

document.getElementById('btn-volver-login').addEventListener('click', () => {
  mostrarPanel('login');
});

document.getElementById('btn-entrar').addEventListener('click', async () => {
  const pin = document.getElementById('pin-input').value.trim();
  const statusEl = document.getElementById('pin-status');
  if (!personaEligiendo) return;
  statusEl.textContent = '';
  const result = await window.nicos.identityLogin(personaEligiendo.user_id, pin);
  if (!result.ok) {
    statusEl.textContent = result.error || 'No se pudo entrar.';
    statusEl.style.color = 'var(--red)';
    return;
  }
  // Éxito: main process ya cargó la pantalla real del rol correspondiente.
});

document.getElementById('btn-completar-alta').addEventListener('click', async () => {
  const statusEl = document.getElementById('alta-status');
  const nombre = document.getElementById('alta-nombre').value.trim();
  const dni = document.getElementById('alta-dni').value.trim();
  const nacimiento = document.getElementById('alta-nacimiento').value;
  const sexo = document.getElementById('alta-sexo').value;
  const pin = document.getElementById('alta-pin').value.trim();
  const codigo = document.getElementById('alta-codigo').value.trim();

  const config = await window.nicos.getMaskedSettings();
  const ipInput = document.getElementById('alta-ip').value.trim();
  const host = config.MAC_LAN_HOST || ipInput;
  const port = config.MAC_LAN_PORT || '47500';

  if (!nombre || !dni || !nacimiento || !sexo || !pin || !codigo || !host) {
    statusEl.textContent = 'Completá todos los campos.';
    statusEl.style.color = 'var(--red)';
    return;
  }
  if (!/^\d{4}$/.test(pin)) {
    statusEl.textContent = 'El PIN tiene que ser de 4 dígitos.';
    statusEl.style.color = 'var(--red)';
    return;
  }

  statusEl.textContent = 'Completando alta...';
  statusEl.style.color = 'var(--muted)';
  try {
    const result = await window.nicos.identityCompleteAlta({
      host, port, code: codigo, deviceName: `PC de ${nombre}`,
      displayName: nombre, dni, fechaNacimiento: nacimiento, sexo, pin,
    });
    if (!result.ok) {
      statusEl.textContent = result.error || 'No se pudo completar el alta.';
      statusEl.style.color = 'var(--red)';
      return;
    }
    // Éxito: main process ya cargó la pantalla real del rol correspondiente.
  } catch (e) {
    statusEl.textContent = e.message || 'No se pudo conectar con la Mac.';
    statusEl.style.color = 'var(--red)';
  }
});

init();
