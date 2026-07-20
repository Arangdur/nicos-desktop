// "Acerca de NicOS" (v0.2.1-rc7) — compartido entre Director y Operativa.
// Todo lo que se muestra acá viene de window.nicos.getAboutInfo() (main.js),
// que a su vez combina build-info.json (generado en cada compilación, ver
// scripts/generate-build-info.js) con lo que solo se sabe en tiempo de
// ejecución. Nunca se piden ni se muestran secretos -- ver el detalle de qué
// se excluye a propósito en cada bloque.

function escHtmlAbout(s) {
  return String(s ?? '').replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function _rolLabel(role) {
  if (role === 'director') return 'Director (Nicolás)';
  if (role === 'operativa') return 'Operativa (Marianela)';
  return 'Sin definir';
}

function _boolTag(value, textoSi, textoNo) {
  if (value === null || value === undefined) return '<span class="tag proceso">Desconocido</span>';
  return value
    ? `<span class="tag resuelto">${escHtmlAbout(textoSi)}</span>`
    : `<span class="tag nuevo">${escHtmlAbout(textoNo)}</span>`;
}

async function renderAboutPanel(containerEl) {
  containerEl.innerHTML = '<div class="empty">Cargando...</div>';
  const info = await window.nicos.getAboutInfo();
  const build = info.build;

  const buildBlock = build ? `
    <dl class="kv-grid">
      <dt>Versión</dt><dd>${escHtmlAbout(build.version)}</dd>
      <dt>Commit</dt><dd class="mono">${escHtmlAbout(build.commit_sha_short)}</dd>
      ${build.git_dirty ? '<dt></dt><dd><span class="tag nuevo">con cambios sin commitear</span></dd>' : ''}
      <dt>Fecha de build</dt><dd>${escHtmlAbout(new Date(build.build_date).toLocaleString('es-AR'))}</dd>
      <dt>Hash de risk_policy.yaml</dt><dd class="mono">${escHtmlAbout(build.risk_policy_sha256)}</dd>
    </dl>
  ` : `
    <div class="empty">Sin datos de compilación -- esto es normal corriendo desde el código fuente (npx electron .) en vez de un paquete instalado. Se genera con "npm run dist:mac" / "dist:win".</div>
  `;

  let coreBlock = '';
  if (info.role === 'director') {
    const core = info.core;
    if (core && core.ok) {
      coreBlock = `
        <h3>Core y Tailscale (esta Mac)</h3>
        <dl class="kv-grid">
          <dt>Core (sidecar)</dt><dd>${_boolTag(core.core_running, 'Activo', 'Detenido')}</dd>
          <dt>Tailscale configurado</dt><dd>${_boolTag(core.tailscale_configured, 'Sí', 'No')}</dd>
          <dt>Tailscale conectado</dt><dd>${_boolTag(core.tailscale_connected, 'Conectado', 'Desconectado')}</dd>
          <dt>Política de riesgo</dt><dd>versión ${escHtmlAbout(core.policy_version)} — <span class="mono">${escHtmlAbout(core.policy_hash)}</span></dd>
          <dt>Python (sidecar)</dt><dd>${escHtmlAbout(core.python_version)}</dd>
        </dl>
      `;
    } else {
      coreBlock = `
        <h3>Core y Tailscale (esta Mac)</h3>
        <div class="error-box">${escHtmlAbout((core && core.error) || 'No se pudo consultar el estado del Core.')}</div>
      `;
    }
  }

  containerEl.innerHTML = `
    <div class="card">
      <h3>NicOS Desktop</h3>
      <dl class="kv-grid"><dt>Edición</dt><dd>${escHtmlAbout(_rolLabel(info.role))}</dd></dl>
    </div>

    <div class="card">
      <h3>Versión y compilación</h3>
      ${buildBlock}
    </div>

    <div class="card">
      <h3>Entorno de ejecución</h3>
      <dl class="kv-grid">
        <dt>Plataforma</dt><dd>${escHtmlAbout(info.platform)} / ${escHtmlAbout(info.arch)}</dd>
        <dt>Electron</dt><dd>${escHtmlAbout(info.electron_version)}</dd>
        <dt>Node</dt><dd>${escHtmlAbout(info.node_version)}</dd>
      </dl>
    </div>

    ${coreBlock ? `<div class="card">${coreBlock}</div>` : ''}
  `;
}
