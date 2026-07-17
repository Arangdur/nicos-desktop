// Puente seguro: el renderer NUNCA tiene acceso directo a Node/fs/child_process.
// Solo puede llamar estas funciones puntuales, que a su vez pasan por ipcMain en main.js.
const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('nicos', {
  getMaskedSettings: () => ipcRenderer.invoke('nicos:get-masked-settings'),
  saveSettings: (update) => ipcRenderer.invoke('nicos:save-settings', update),
  setRole: (role) => ipcRenderer.invoke('nicos:set-role', role),
  getSidecarPort: () => ipcRenderer.invoke('nicos:get-sidecar-port'),
});
