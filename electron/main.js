const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');
const fs = require('fs');

const settingsStore = require('./settings-store');
const sidecar = require('./sidecar-manager');
const operativaClient = require('./operativa-client');

let mainWindow = null;
let outboxFlushInterval = null;

function _envFromConfig(config) {
  const env = {};
  if (config.ANTHROPIC_API_KEY) env.ANTHROPIC_API_KEY = config.ANTHROPIC_API_KEY;
  if (config.OPENAI_API_KEY) env.OPENAI_API_KEY = config.OPENAI_API_KEY;
  if (config.GOOGLE_SERVICE_ACCOUNT_JSON) env.GOOGLE_SERVICE_ACCOUNT_JSON = config.GOOGLE_SERVICE_ACCOUNT_JSON;
  if (config.WHATSAPP_SHEET_ID) env.WHATSAPP_SHEET_ID = config.WHATSAPP_SHEET_ID;
  if (config.ANTHROPIC_MODEL) env.ANTHROPIC_MODEL = config.ANTHROPIC_MODEL;
  if (config.OPENAI_MODEL) env.OPENAI_MODEL = config.OPENAI_MODEL;
  if (config.TAILSCALE_IP) env.NICOS_TAILSCALE_IP = config.TAILSCALE_IP;
  return env;
}

function _startOutboxFlusher() {
  if (outboxFlushInterval) return;
  outboxFlushInterval = setInterval(async () => {
    const result = await operativaClient.flushOutbox();
    if (result.flushed > 0) {
      console.log(`[main] outbox: ${result.flushed} tarea(s) enviada(s) a la Mac`);
    }
  }, 30000);
}

async function _bootAndLoad() {
  const config = settingsStore.getDecryptedConfig();
  const role = config.role || null;
  let port = null;

  // CLAVE: solo el rol Director levanta el sidecar Python (con todos los secretos).
  // El rol Operativa NUNCA corre ese proceso en su máquina — solo habla por HTTP
  // a la Mac (ver operativa-client.js), así que no hay ningún secreto que pueda
  // terminar en la PC de Marianela ni siquiera por error de configuración.
  if (role === 'director') {
    try {
      port = await sidecar.startSidecar(_envFromConfig(config));
    } catch (err) {
      console.error('[main] no se pudo arrancar el sidecar:', err);
    }
  } else if (role === 'operativa') {
    _startOutboxFlusher();
  }

  const query = new URLSearchParams({ port: port || '', role: role || '' }).toString();

  if (!role) {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'selector.html'), { search: query });
  } else if (role === 'director') {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'director', 'index.html'), { search: query });
  } else if (!config.PAIRED_DEVICE_TOKEN) {
    // Operativa sin vincular todavía -> pantalla de pairing, no la app directamente.
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'operativa', 'pairing.html'), { search: query });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'operativa', 'index.html'), { search: query });
  }
}

function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 820,
    minWidth: 960,
    minHeight: 640,
    title: 'NicOS Desktop',
    webPreferences: {
      preload: path.join(__dirname, 'preload.js'),
      contextIsolation: true,
      nodeIntegration: false,
    },
  });

  _bootAndLoad();
}

app.whenReady().then(() => {
  createWindow();
  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  sidecar.stopSidecar();
  if (outboxFlushInterval) clearInterval(outboxFlushInterval);
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  sidecar.stopSidecar();
  if (outboxFlushInterval) clearInterval(outboxFlushInterval);
});

// ---- IPC comunes ----

ipcMain.handle('nicos:get-masked-settings', () => settingsStore.getMaskedConfig());

ipcMain.handle('nicos:save-settings', async (_event, update) => {
  settingsStore.saveSettings(update);
  const config = settingsStore.getDecryptedConfig();
  let port = null;
  if (config.role === 'director') {
    port = await sidecar.restartSidecar(_envFromConfig(config));
  }
  return { ok: true, port };
});

ipcMain.handle('nicos:set-role', async (_event, role) => {
  // v0.2.1: una vez que esta PC eligió "operativa", no se puede volver a
  // "director" desde la UI -- si es un error real (esta es la Mac de
  // Nicolás), la salida deliberada es borrar el archivo de configuración a
  // mano y reiniciar, no un botón de la app. Esto es la restricción de rol
  // aplicada en el proceso principal, no solo escondida en el renderer.
  const currentRole = settingsStore.getRole();
  if (currentRole === 'operativa' && role === 'director') {
    throw new Error(
      'Esta PC ya está configurada como Operativa — no se puede cambiar a Director desde acá. ' +
      'Si esto es un error, borrá el archivo de configuración de NicOS a mano y reiniciá la app.'
    );
  }
  settingsStore.saveSettings({ role });
  if (mainWindow) await _bootAndLoad();
  return { ok: true };
});

ipcMain.handle('nicos:get-sidecar-port', () => sidecar.getPort());

// v0.2.1-rc7 -- "Acerca de NicOS". `build-info.json` lo genera
// scripts/generate-build-info.js en cada compilación (nunca a mano) y queda
// embebido en el paquete -- acá se combina con lo que solo se puede saber en
// tiempo de ejecución (versión real de Electron/Node, plataforma/arquitectura
// de ESTA máquina, rol de ESTA instalación, y -- solo si es Director -- el
// estado del Core/Tailscale, consultado al sidecar local, nunca a la red).
function _readBuildInfo() {
  // Empaquetado: extraResource plano (ver package.json -> build.mac/win.
  // extraResources) -- deliberadamente FUERA del asar, así los scripts de
  // exportación de logs (bash/PowerShell) pueden leerlo con un `cat`/
  // `Get-Content` común, sin depender de herramientas para inspeccionar asar.
  const buildInfoPath = app.isPackaged
    ? path.join(process.resourcesPath, 'build-info.json')
    : path.join(app.getAppPath(), 'build', 'build-info.json');
  try {
    return JSON.parse(fs.readFileSync(buildInfoPath, 'utf-8'));
  } catch (e) {
    return null;
  }
}

ipcMain.handle('nicos:get-about-info', async () => {
  const role = settingsStore.getRole();
  const info = {
    role,
    app_version: app.getVersion(),
    electron_version: process.versions.electron,
    node_version: process.versions.node,
    platform: process.platform,
    arch: process.arch,
    build: _readBuildInfo(),
    core: null,
  };

  if (role === 'director') {
    const port = sidecar.getPort();
    if (port) {
      try {
        const res = await fetch(`http://127.0.0.1:${port}/api/v1/system/status`);
        info.core = await res.json();
      } catch (e) {
        info.core = { ok: false, error: 'No se pudo consultar el estado del Core.' };
      }
    } else {
      info.core = { ok: false, error: 'El sidecar no está corriendo.' };
    }
  }

  return info;
});

// ---- IPC exclusivo de la vista Operativa (el token nunca sale de main process) ----

ipcMain.handle('nicos:operativa-pair', async (_event, { host, port, code, deviceName }) => {
  await operativaClient.pairWithMac(host, port, code, deviceName);
  if (mainWindow) await _bootAndLoad();
  return { ok: true };
});

ipcMain.handle('nicos:operativa-submit-task', (_event, rawText) => operativaClient.submitTask(rawText));
ipcMain.handle('nicos:operativa-list-tasks', () => operativaClient.listTasks());
ipcMain.handle('nicos:operativa-flush-outbox', () => operativaClient.flushOutbox());
ipcMain.handle('nicos:operativa-outbox-count', () => operativaClient.getOutboxCount());
ipcMain.handle('nicos:operativa-list-messages', () => operativaClient.listMessages());
ipcMain.handle('nicos:operativa-update-message', (_event, { row, updates }) =>
  operativaClient.updateMessage(row, updates));

ipcMain.handle('nicos:operativa-forget-pairing', () => {
  settingsStore.clearOperativaPairing();
  return { ok: true };
});
