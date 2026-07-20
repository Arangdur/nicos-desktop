// Puente seguro: el renderer NUNCA tiene acceso directo a Node/fs/child_process/fetch-con-token.
// Solo puede llamar estas funciones puntuales, que a su vez pasan por ipcMain en main.js.
// Notar que ninguna de las funciones de "operativa-*" devuelve ni recibe un secreto —
// el token de dispositivo vive y se usa enteramente dentro de main.js/operativa-client.js.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nicos', {
  getMaskedSettings: () => ipcRenderer.invoke('nicos:get-masked-settings'),
  saveSettings: (update) => ipcRenderer.invoke('nicos:save-settings', update),
  setRole: (role) => ipcRenderer.invoke('nicos:set-role', role),
  getSidecarPort: () => ipcRenderer.invoke('nicos:get-sidecar-port'),
  getAboutInfo: () => ipcRenderer.invoke('nicos:get-about-info'),

  identityList: () => ipcRenderer.invoke('nicos:identity-list'),
  identityActive: () => ipcRenderer.invoke('nicos:identity-active'),
  identityCompleteAlta: (payload) => ipcRenderer.invoke('nicos:identity-complete-alta', payload),
  identityLogin: (userId, pin) => ipcRenderer.invoke('nicos:identity-login', { userId, pin }),
  identityForget: (userId) => ipcRenderer.invoke('nicos:identity-forget', userId),
  identityLogout: () => ipcRenderer.invoke('nicos:identity-logout'),

  operativaSubmitTask: (rawText) => ipcRenderer.invoke('nicos:operativa-submit-task', rawText),
  operativaListTasks: () => ipcRenderer.invoke('nicos:operativa-list-tasks'),
  operativaFlushOutbox: () => ipcRenderer.invoke('nicos:operativa-flush-outbox'),
  operativaOutboxCount: () => ipcRenderer.invoke('nicos:operativa-outbox-count'),
  operativaListMessages: () => ipcRenderer.invoke('nicos:operativa-list-messages'),
  operativaUpdateMessage: (row, updates) => ipcRenderer.invoke('nicos:operativa-update-message', { row, updates }),
});
