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

  // Diagnóstico específico de Tailscale: si la IP no tiene forma de IP de
  // Tailscale (100.64.0.0/10, casi siempre visible como "100.x.y.z" para el
  // usuario), lo más probable es que haya puesto la IP de WiFi de la oficina
  // por error — avisamos antes de intentar, no después de un timeout confuso.
  if (!/^100\.\d{1,3}\.\d{1,3}\.\d{1,3}$/.test(host)) {
    statusEl.textContent =
      'Esa IP no parece ser de Tailscale (debería empezar con "100."). Confirmá con Nicolás ' +
      'la IP que le da el comando "tailscale ip -4" en su Mac, y que ambas máquinas estén ' +
      'conectadas a la misma cuenta de Tailscale.';
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
    statusEl.textContent =
      'No se pudo conectar. Verificá: (1) que Tailscale esté corriendo en esta PC y en la Mac ' +
      '(2) que ambas estén en la misma cuenta (3) que el código no haya vencido (dura 5 min). ' +
      'Detalle: ' + e.message;
    statusEl.style.color = 'var(--red)';
  }
});
