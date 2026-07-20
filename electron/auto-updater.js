// Auto-actualización de las instalaciones no-Director (Operativa, Enfermero) --
// v0.2.2. Decisión de diseño explícita: en vez de un canal de comando remoto
// a medida (Nicolás "empujando" código a la PC de Marianela/Abate en vivo,
// que sería literalmente un mecanismo de ejecución remota si algo sale mal en
// el diseño), se usa electron-updater apuntando a GitHub Releases del mismo
// repo -- el patrón estándar y ya auditado de Electron para esto.
//
// El flujo real: Nicolás pushea un tag de versión nueva desde su Mac -> el
// workflow de GitHub Actions (build-windows.yml) compila el .exe y lo publica
// como Release -> cada PC no-Director (con este módulo activo) puede
// consultar si hay una versión más nueva y, si la persona confirma, bajarla e
// instalarla -- sin que Nicolás tenga que ir físicamente a la PC de
// enfermería de Abate cada vez que hay una corrección.
//
// Nunca corre en la Mac de Nicolás (Director) ni en modo desarrollo -- ver el
// guard en main.js. Nunca se auto-instala sin que la persona lo confirme
// (autoDownload=false) -- coherente con el resto del proyecto: nada
// silencioso que cambie el comportamiento de la app sin que quede a la vista.
const { autoUpdater } = require('electron-updater');

autoUpdater.autoDownload = false;
autoUpdater.autoInstallOnAppQuit = true;

let mainWindowRef = null;

function _send(status, extra = {}) {
  if (mainWindowRef && !mainWindowRef.isDestroyed()) {
    mainWindowRef.webContents.send('nicos:update-status', { status, ...extra });
  }
}

function init(mainWindow) {
  mainWindowRef = mainWindow;

  autoUpdater.on('checking-for-update', () => _send('checking'));
  autoUpdater.on('update-available', (info) => _send('available', { version: info.version }));
  autoUpdater.on('update-not-available', () => _send('up-to-date'));
  autoUpdater.on('download-progress', (progress) => _send('downloading', { percent: Math.round(progress.percent) }));
  autoUpdater.on('update-downloaded', (info) => _send('downloaded', { version: info.version }));
  autoUpdater.on('error', (err) => _send('error', { message: err == null ? 'Error desconocido' : err.message }));
}

async function checkForUpdates() {
  try {
    await autoUpdater.checkForUpdates();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

async function downloadUpdate() {
  try {
    await autoUpdater.downloadUpdate();
    return { ok: true };
  } catch (e) {
    return { ok: false, error: e.message };
  }
}

function quitAndInstall() {
  autoUpdater.quitAndInstall();
}

module.exports = { init, checkForUpdates, downloadUpdate, quitAndInstall };
