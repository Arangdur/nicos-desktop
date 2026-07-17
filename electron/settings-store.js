// Almacenamiento de configuración — separado por perfil de rol, a propósito.
//
// Perfil Director (Nicolás, Mac): TODOS los secretos de IA/Google viven acá,
// cifrados con safeStorage (Keychain). Es el único perfil con esta capacidad.
//
// Perfil Operativa (Marianela, Windows): NUNCA guarda ninguna API key ni
// credencial de Google — solo el token de dispositivo emitido por el pairing
// (también cifrado con safeStorage/DPAPI) y la dirección LAN de la Mac. No hay
// forma de que un secreto termine en esta máquina porque el código ni siquiera
// tiene un campo para escribirlo (ver settings-panel-operativa.js).
const { app, safeStorage } = require('electron');
const fs = require('fs');
const path = require('path');

const SETTINGS_PATH = path.join(app.getPath('userData'), 'nicos-settings.json');

const DIRECTOR_SECRET_KEYS = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_SERVICE_ACCOUNT_JSON',
];
const DIRECTOR_PLAIN_KEYS = ['WHATSAPP_SHEET_ID', 'ANTHROPIC_MODEL', 'OPENAI_MODEL'];

// El token de dispositivo ES un secreto (da acceso a la Mac) — se cifra igual
// que las API keys, aunque conceptualmente sea "de otro tipo".
const OPERATIVA_SECRET_KEYS = ['PAIRED_DEVICE_TOKEN'];
const OPERATIVA_PLAIN_KEYS = ['MAC_LAN_HOST', 'MAC_LAN_PORT', 'PAIRED_DEVICE_ID', 'PAIRED_DEVICE_NAME'];

const COMMON_PLAIN_KEYS = ['role'];

function _readRaw() {
  if (!fs.existsSync(SETTINGS_PATH)) return {};
  try {
    return JSON.parse(fs.readFileSync(SETTINGS_PATH, 'utf-8'));
  } catch (e) {
    console.error('[settings-store] archivo corrupto, se reinicia:', e);
    return {};
  }
}

function _writeRaw(obj) {
  fs.mkdirSync(path.dirname(SETTINGS_PATH), { recursive: true });
  fs.writeFileSync(SETTINGS_PATH, JSON.stringify(obj, null, 2), 'utf-8');
}

function _keysForRole(role) {
  if (role === 'director') return { secret: DIRECTOR_SECRET_KEYS, plain: DIRECTOR_PLAIN_KEYS };
  return { secret: OPERATIVA_SECRET_KEYS, plain: OPERATIVA_PLAIN_KEYS };
}

function getRole() {
  return _readRaw().role || null;
}

// Config completa DESCIFRADA — solo para uso interno de main process (armar env
// vars del sidecar si es Director, o headers de fetch si es Operativa).
function getDecryptedConfig() {
  const raw = _readRaw();
  // OJO: el default 'director' es solo para saber qué lista de claves leer del
  // disco cuando todavía no hay rol guardado (no importa cuál, raw estará vacío
  // igual) — el `role` que se DEVUELVE tiene que ser el real (o null), nunca el
  // default, porque main.js decide si mostrar el selector según `if (!role)`.
  const { secret, plain } = _keysForRole(raw.role || 'director');
  const result = { role: raw.role || null };
  for (const key of [...COMMON_PLAIN_KEYS, ...plain]) {
    if (raw[key] !== undefined) result[key] = raw[key];
  }
  for (const key of secret) {
    if (raw[key] && safeStorage.isEncryptionAvailable()) {
      try {
        result[key] = safeStorage.decryptString(Buffer.from(raw[key], 'base64'));
      } catch (e) {
        console.error(`[settings-store] no se pudo descifrar ${key}:`, e);
      }
    }
  }
  return result;
}

// Config para el renderer: los secretos vienen enmascarados (solo "configurado: sí/no").
// CRÍTICO: esta función nunca devuelve un secreto en texto plano, ni siquiera al
// propio renderer de esa misma instalación — el renderer no tiene por qué verlo.
function getMaskedConfig() {
  const raw = _readRaw();
  const role = raw.role || null;
  const { secret, plain } = role ? _keysForRole(role) : { secret: [], plain: [] };
  const result = { role };
  for (const key of plain) {
    result[key] = raw[key] !== undefined ? raw[key] : null;
  }
  for (const key of secret) {
    result[key + '_configurado'] = Boolean(raw[key]);
  }
  return result;
}

function saveSettings(update) {
  const raw = _readRaw();
  const role = update.role || raw.role || 'director';
  const { secret, plain } = _keysForRole(role);

  if (update.role !== undefined) raw.role = update.role;

  for (const key of plain) {
    if (update[key] !== undefined) raw[key] = update[key];
  }
  for (const key of secret) {
    if (update[key] !== undefined && update[key] !== '') {
      if (!safeStorage.isEncryptionAvailable()) {
        throw new Error(
          'El cifrado seguro del sistema operativo no está disponible en esta máquina — ' +
          'no se puede guardar de forma segura. Contactar soporte técnico.'
        );
      }
      raw[key] = safeStorage.encryptString(update[key]).toString('base64');
    }
  }
  _writeRaw(raw);
}

function clearOperativaPairing() {
  const raw = _readRaw();
  for (const key of [...OPERATIVA_SECRET_KEYS, ...OPERATIVA_PLAIN_KEYS]) {
    delete raw[key];
  }
  _writeRaw(raw);
}

module.exports = {
  getRole, getDecryptedConfig, getMaskedConfig, saveSettings, clearOperativaPairing,
  DIRECTOR_SECRET_KEYS, DIRECTOR_PLAIN_KEYS, OPERATIVA_SECRET_KEYS, OPERATIVA_PLAIN_KEYS,
};
