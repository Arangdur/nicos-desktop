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
    <tr><td>Versión</td><td>${escHtmlAbout(build.version)}</td></tr>
    <tr><td>Commit</td><td><code>${escHtmlAbout(build.commit_sha_short)}</code>${build.git_dirty ? ' <span class="tag nuevo">con cambios sin commitear</span>' : ''}</td></tr>
    <tr><td>Fecha de build</td><td>${escHtmlAbout(new Date(build.build_date).toLocaleString('es-AR'))}</td></tr>
    <tr><td>Hash de risk_policy.yaml</td><td><code style="font-size:11px;">${escHtmlAbout(build.risk_policy_sha256)}</code></td></tr>
  ` : `
    <tr><td colspan="2"><div class="empty">Sin datos de compilación -- esto es normal corriendo desde el código fuente (npx electron .) en vez de un paquete instalado. Se genera con "npm run dist:mac" / "dist:win".</div></td></tr>
  `;

  let coreBlock = '';
  if (info.role === 'director') {
    const core = info.core;
    if (core && core.ok) {
      coreBlock = `
        <h3>Core y Tailscale (esta Mac)</h3>
        <table>
          <tr><td>Core (sidecar)</td><td>${_boolTag(core.core_running, 'Activo', 'Detenido')}</td></tr>
          <tr><td>Tailscale configurado</td><td>${_boolTag(core.tailscale_configured, 'Sí', 'No')}</td></tr>
          <tr><td>Tailscale conectado</td><td>${_boolTag(core.tailscale_connected, 'Conectado', 'Desconectado')}</td></tr>
          <tr><td>Política de riesgo</td><td>versión ${escHtmlAbout(core.policy_version)} — <code style="font-size:11px;">${escHtmlAbout(core.policy_hash)}</code></td></tr>
          <tr><td>Python (sidecar)</td><td>${escHtmlAbout(core.python_version)}</td></tr>
        </table>
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
      <table>
        <tr><td>Edición</td><td>${escHtmlAbout(_rolLabel(info.role))}</td></tr>
      </table>
    </div>

    <div class="card">
      <h3>Versión y compilación</h3>
      <table>${buildBlock}</table>
    </div>

    <div class="card">
      <h3>Entorno de ejecución</h3>
      <table>
        <tr><td>Plataforma</td><td>${escHtmlAbout(info.platform)} / ${escHtmlAbout(info.arch)}</td></tr>
        <tr><td>Electron</td><td>${escHtmlAbout(info.electron_version)}</td></tr>
        <tr><td>Node</td><td>${escHtmlAbout(info.node_version)}</td></tr>
      </table>
    </div>

    ${coreBlock ? `<div class="card">${coreBlock}</div>` : ''}
  `;
}
