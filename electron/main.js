const { app, BrowserWindow, ipcMain } = require('electron');
const path = require('path');

const settingsStore = require('./settings-store');
const sidecar = require('./sidecar-manager');

let mainWindow = null;

function _envFromConfig(config) {
  const env = {};
  if (config.ANTHROPIC_API_KEY) env.ANTHROPIC_API_KEY = config.ANTHROPIC_API_KEY;
  if (config.OPENAI_API_KEY) env.OPENAI_API_KEY = config.OPENAI_API_KEY;
  if (config.GOOGLE_SERVICE_ACCOUNT_JSON) env.GOOGLE_SERVICE_ACCOUNT_JSON = config.GOOGLE_SERVICE_ACCOUNT_JSON;
  if (config.WHATSAPP_SHEET_ID) env.WHATSAPP_SHEET_ID = config.WHATSAPP_SHEET_ID;
  if (config.ANTHROPIC_MODEL) env.ANTHROPIC_MODEL = config.ANTHROPIC_MODEL;
  if (config.OPENAI_MODEL) env.OPENAI_MODEL = config.OPENAI_MODEL;
  return env;
}

async function _bootSidecarAndLoad() {
  const config = settingsStore.getDecryptedConfig();
  let port;
  try {
    port = await sidecar.startSidecar(_envFromConfig(config));
  } catch (err) {
    console.error('[main] no se pudo arrancar el sidecar:', err);
    port = null;
  }

  const role = config.role || null;
  const query = new URLSearchParams({ port: port || '', role: role || '' }).toString();

  if (!role) {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'selector.html'), {
      search: query,
    });
  } else if (role === 'director') {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'director', 'index.html'), {
      search: query,
    });
  } else {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'operativa', 'index.html'), {
      search: query,
    });
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

  _bootSidecarAndLoad();
}

app.whenReady().then(() => {
  createWindow();

  app.on('activate', () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on('window-all-closed', () => {
  sidecar.stopSidecar();
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  sidecar.stopSidecar();
});

// ---- IPC: puente seguro entre el renderer (sin acceso a Node) y este proceso ----

ipcMain.handle('nicos:get-masked-settings', () => settingsStore.getMaskedConfig());

ipcMain.handle('nicos:save-settings', async (_event, update) => {
  settingsStore.saveSettings(update);
  const config = settingsStore.getDecryptedConfig();
  const port = await sidecar.restartSidecar(_envFromConfig(config));
  return { ok: true, port };
});

ipcMain.handle('nicos:set-role', async (_event, role) => {
  settingsStore.saveSettings({ role });
  if (mainWindow) await _bootSidecarAndLoad();
  return { ok: true };
});

ipcMain.handle('nicos:get-sidecar-port', () => sidecar.getPort());
