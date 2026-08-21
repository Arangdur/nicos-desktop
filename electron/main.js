const { app, BrowserWindow, ipcMain, shell } = require('electron');
const path = require('path');
const fs = require('fs');

const settingsStore = require('./settings-store');
const sidecar = require('./sidecar-manager');
const operativaClient = require('./operativa-client');
const autoUpdaterModule = require('./auto-updater');

let mainWindow = null;
let outboxFlushInterval = null;
let updateCheckInterval = null;

// v0.2.2 -- auto-actualización SOLO para instalaciones no-Director, y SOLO
// empaquetadas (app.isPackaged) -- en desarrollo no hay ningún feed de
// actualizaciones real, y la Mac de Nicolás nunca se auto-actualiza así (él
// la actualiza directamente, corriendo el repo).
function _maybeInitAutoUpdater(role) {
  if (role === 'director' || !app.isPackaged) return;
  autoUpdaterModule.init(mainWindow);
  if (!updateCheckInterval) {
    updateCheckInterval = setInterval(() => autoUpdaterModule.checkForUpdates(), 4 * 60 * 60 * 1000);
  }
}

function _envFromConfig(config) {
  const env = {};
  // v0.2.7 (21/08) -- hallazgo real empaquetando el primer .dmg real para
  // Nicolás: sin esto, el sidecar (PyInstaller onefile) guarda nicos.db al
  // lado de su propio __file__, que en modo empaquetado resuelve a la
  // carpeta de extracción TEMPORAL de PyInstaller -- se borra al cerrar la
  // app, así que todos los datos (WhatsApp, turnos, facturas) se perderían
  // en cada reinicio, no solo en cada actualización. En desarrollo
  // (npm start, app.isPackaged=false) esto no se toca -- sigue usando
  // sidecar/nicos.db de siempre, mismo comportamiento que toda la sesión.
  if (app.isPackaged) {
    env.NICOS_DB_PATH = path.join(app.getPath('userData'), 'nicos.db');
  }
  if (config.ANTHROPIC_API_KEY) env.ANTHROPIC_API_KEY = config.ANTHROPIC_API_KEY;
  if (config.OPENAI_API_KEY) env.OPENAI_API_KEY = config.OPENAI_API_KEY;
  if (config.ANTHROPIC_MODEL) env.ANTHROPIC_MODEL = config.ANTHROPIC_MODEL;
  if (config.OPENAI_MODEL) env.OPENAI_MODEL = config.OPENAI_MODEL;
  if (config.TAILSCALE_IP) env.NICOS_TAILSCALE_IP = config.TAILSCALE_IP;
  if (config.TWILIO_ACCOUNT_SID) env.TWILIO_ACCOUNT_SID = config.TWILIO_ACCOUNT_SID;
  if (config.TWILIO_AUTH_TOKEN) env.TWILIO_AUTH_TOKEN = config.TWILIO_AUTH_TOKEN;
  if (config.TWILIO_WHATSAPP_FROM) env.TWILIO_WHATSAPP_FROM = config.TWILIO_WHATSAPP_FROM;
  if (config.DRAPP_API_KEY) env.DRAPP_API_KEY = config.DRAPP_API_KEY;
  if (config.DRAPP_TEAM_ID) env.DRAPP_TEAM_ID = config.DRAPP_TEAM_ID;
  if (config.DRAPP_RESOURCE_ID) env.DRAPP_RESOURCE_ID = config.DRAPP_RESOURCE_ID;
  if (config.DRAPP_SERVICE_KEY_MEDICINA_GENERAL) env.DRAPP_SERVICE_KEY_MEDICINA_GENERAL = config.DRAPP_SERVICE_KEY_MEDICINA_GENERAL;
  if (config.DRAPP_SERVICE_KEY_PSIQUIATRIA) env.DRAPP_SERVICE_KEY_PSIQUIATRIA = config.DRAPP_SERVICE_KEY_PSIQUIATRIA;
  if (config.TWILIO_WEBHOOK_BASE_URL) env.NICOS_TWILIO_WEBHOOK_BASE_URL = config.TWILIO_WEBHOOK_BASE_URL;
  if (config.FACTURACION_TELEFONOS_AUTORIZADOS) env.NICOS_FACTURACION_TELEFONOS_AUTORIZADOS = config.FACTURACION_TELEFONOS_AUTORIZADOS;
  if (config.CONSULTORIO_WHATSAPP_NUMERO) env.CONSULTORIO_WHATSAPP_NUMERO = config.CONSULTORIO_WHATSAPP_NUMERO;
  if (config.GMAIL_CLIENT_ID) env.GMAIL_CLIENT_ID = config.GMAIL_CLIENT_ID;
  if (config.GMAIL_CLIENT_SECRET) env.GMAIL_CLIENT_SECRET = config.GMAIL_CLIENT_SECRET;
  if (config.GMAIL_REFRESH_TOKEN_CONSULTORIO) env.GMAIL_REFRESH_TOKEN_CONSULTORIO = config.GMAIL_REFRESH_TOKEN_CONSULTORIO;
  if (config.GMAIL_REFRESH_TOKEN_ABATE) env.GMAIL_REFRESH_TOKEN_ABATE = config.GMAIL_REFRESH_TOKEN_ABATE;
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
    // Elección de MÁQUINA, una sola vez: ¿esta PC es la Mac de Nicolás o no?
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'selector.html'), { search: query });
  } else if (role === 'director') {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'director', 'index.html'), { search: query });
  } else {
    // v0.2.2 -- toda PC no-Director pasa SIEMPRE por el login de persona
    // primero (¿quién sos, de las ya vinculadas acá? / soy nuevo, tengo un
    // código) -- nunca salta directo a la app real, ni siquiera si ya hay
    // identidades guardadas, porque en un dispositivo compartido (ej. la PC
    // de enfermería) puede haber más de una persona y cada arranque hay que
    // preguntar de nuevo quién lo está usando ahora.
    operativaClient.logout();
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'login.html'), { search: query });
  }

  _maybeInitAutoUpdater(role);
}

function _loadRoleScreen(role) {
  const query = new URLSearchParams({ role: role || '' }).toString();
  if (role === 'operativa') {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'operativa', 'index.html'), { search: query });
  } else if (role === 'enfermero') {
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'enfermero', 'index.html'), { search: query });
  } else {
    // Rol desconocido -- no debería pasar (login.js solo llega acá tras un
    // login/alta exitoso), pero si pasa, mejor volver al login que romper.
    mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'login.html'));
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

  // v0.2.6 -- un target="_blank" (ej. "Ver PDF" de una factura emitida) por
  // default intenta abrir una ventana de Electron nueva sin barra de
  // direcciones ni forma de cerrarla bien. Se manda al navegador real del
  // sistema en su lugar -- patrón estándar de Electron, no hay nada
  // propio que mantener.
  mainWindow.webContents.setWindowOpenHandler(({ url }) => {
    shell.openExternal(url);
    return { action: 'deny' };
  });

  _bootAndLoad();
}

// Sin esto, abrir la app dos veces (doble-click de nuevo, relaunch) levanta
// dos procesos Electron, cada uno con su propio sidecar Python -- root cause
// del bug de "procesos duplicados". requestSingleInstanceLock mata el
// segundo intento y en vez de eso enfoca la ventana que ya está abierta.
if (!app.requestSingleInstanceLock()) {
  app.quit();
} else {
  app.on('second-instance', () => {
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });

  app.whenReady().then(() => {
    createWindow();
    app.on('activate', () => {
      if (BrowserWindow.getAllWindows().length === 0) createWindow();
    });
  });
}

app.on('window-all-closed', () => {
  sidecar.stopSidecar();
  if (outboxFlushInterval) clearInterval(outboxFlushInterval);
  if (updateCheckInterval) clearInterval(updateCheckInterval);
  if (process.platform !== 'darwin') app.quit();
});

app.on('before-quit', () => {
  sidecar.stopSidecar();
  if (outboxFlushInterval) clearInterval(outboxFlushInterval);
  if (updateCheckInterval) clearInterval(updateCheckInterval);
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

// ---- IPC de identidad (login/alta) -- el token nunca sale de main process ----
// v0.2.2 -- reemplaza el viejo par pairing/forget-pairing de un solo token por
// operaciones sobre la LISTA de identidades de esta PC (ver operativa-client.js).

ipcMain.handle('nicos:identity-list', () => operativaClient.listIdentities());
ipcMain.handle('nicos:identity-active', () => operativaClient.getActiveIdentity());

ipcMain.handle('nicos:identity-complete-alta', async (_event, payload) => {
  const result = await operativaClient.completeAlta(payload);
  if (mainWindow) _loadRoleScreen(result.role);
  return result;
});

ipcMain.handle('nicos:identity-login', async (_event, { userId, pin }) => {
  const result = await operativaClient.loginWithPin(userId, pin);
  if (result.ok && mainWindow) _loadRoleScreen(result.role);
  return result;
});

ipcMain.handle('nicos:identity-forget', (_event, userId) => operativaClient.forgetIdentity(userId));

ipcMain.handle('nicos:identity-logout', async () => {
  operativaClient.logout();
  if (mainWindow) mainWindow.loadFile(path.join(__dirname, '..', 'renderer', 'shared', 'login.html'));
  return { ok: true };
});

// ---- IPC de auto-actualización (solo relevante fuera de la Mac de Nicolás) ----

ipcMain.handle('nicos:check-for-updates', () => autoUpdaterModule.checkForUpdates());
ipcMain.handle('nicos:download-update', () => autoUpdaterModule.downloadUpdate());
ipcMain.handle('nicos:quit-and-install-update', () => autoUpdaterModule.quitAndInstall());

// ---- IPC exclusivo de las vistas no-Director (Operativa, Enfermero) ----

ipcMain.handle('nicos:operativa-submit-task', (_event, rawText) => operativaClient.submitTask(rawText));
ipcMain.handle('nicos:operativa-list-tasks', () => operativaClient.listTasks());
ipcMain.handle('nicos:operativa-flush-outbox', () => operativaClient.flushOutbox());
ipcMain.handle('nicos:operativa-outbox-count', () => operativaClient.getOutboxCount());
ipcMain.handle('nicos:recordatorios-list', () => operativaClient.listRecordatorios());
ipcMain.handle('nicos:recordatorios-importar', (_event, turnos) => operativaClient.recordatoriosImportar(turnos));
ipcMain.handle('nicos:recordatorios-completar-telefono', (_event, { recordatorioId, telefono }) =>
  operativaClient.recordatoriosCompletarTelefono(recordatorioId, telefono));
ipcMain.handle('nicos:whatsapp-mensajes-list', () => operativaClient.whatsappMensajesList());
ipcMain.handle('nicos:whatsapp-mensaje-aprobar', (_event, { mensajeId, textoFinal }) =>
  operativaClient.whatsappMensajeAprobar(mensajeId, textoFinal));
ipcMain.handle('nicos:whatsapp-mensaje-rechazar', (_event, mensajeId) =>
  operativaClient.whatsappMensajeRechazar(mensajeId));

// ---- IPC exclusivo de la vista Enfermero (Enfermería de Abate) ----

ipcMain.handle('nicos:abate-list-residentes', () => operativaClient.abateListResidentes());
ipcMain.handle('nicos:abate-get-tratamiento', (_event, residenteId) => operativaClient.abateGetTratamiento(residenteId));
ipcMain.handle('nicos:abate-list-administraciones-hoy', (_event, residenteId) => operativaClient.abateListAdministracionesHoy(residenteId));
ipcMain.handle('nicos:abate-registrar-administracion', (_event, { residenteId, droga, horarioPrevisto }) =>
  operativaClient.abateRegistrarAdministracion(residenteId, droga, horarioPrevisto));
ipcMain.handle('nicos:abate-list-novedades', (_event, residenteId) => operativaClient.abateListNovedades(residenteId));
ipcMain.handle('nicos:abate-create-novedad', (_event, { residenteId, categoria, texto }) =>
  operativaClient.abateCreateNovedad(residenteId, categoria, texto));
