document.getElementById('btn-pair').addEventListener('click', async () => {
  const host = document.getElementById('mac-host').value.trim();
  const port = document.getElementById('mac-port').value.trim() || '47500';
  const code = document.getElementById('pairing-code').value.trim();
  const deviceName = document.getElementById('device-name').value.trim() || 'PC sin nombre';
  const statusEl = document.getElementById('pairing-status');

  if (!host || !code) {
    statusEl.textContent = 'Completá al menos la IP de la Mac y el código.';
    statusEl.style.color = 'var(--red)';
    return;
  }

  statusEl.textContent = 'Vinculando...';
  statusEl.style.color = 'var(--muted)';
  try {
    await window.nicos.operativaPair(host, port, code, deviceName);
    statusEl.textContent = 'Vinculado con éxito.';
    statusEl.style.color = 'var(--green)';
    // main.js ya recarga la ventana con la app real tras vincular exitosamente.
  } catch (e) {
    statusEl.textContent = 'Error: ' + e.message;
    statusEl.style.color = 'var(--red)';
  }
});
