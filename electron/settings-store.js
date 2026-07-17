// Almacenamiento de configuración: los secretos (API keys, credenciales de Google)
// se cifran con safeStorage del propio SO (Keychain en Mac, DPAPI en Windows) antes
// de tocar el disco — nunca se guardan en texto plano, a diferencia de lo que se
// encontró en el ecosistema original (monitor_bot.py, whatsapp-config.json).
const { app, safeStorage } = require('electron');
const fs = require('fs');
const path = require('path');

const SETTINGS_PATH = path.join(app.getPath('userData'), 'nicos-settings.json');

const SECRET_KEYS = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'GOOGLE_SERVICE_ACCOUNT_JSON',
];

const PLAIN_KEYS = [
  'role',              // 'director' | 'operativa'
  'WHATSAPP_SHEET_ID',
  'ANTHROPIC_MODEL',
  'OPENAI_MODEL',
];

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

// Devuelve la config completa DESCIFRADA — solo se usa internamente en main process
// para armar las env vars del sidecar. Nunca se manda tal cual al renderer.
function getDecryptedConfig() {
  const raw = _readRaw();
  const result = {};
  for (const key of PLAIN_KEYS) {
    if (raw[key] !== undefined) result[key] = raw[key];
  }
  for (const key of SECRET_KEYS) {
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

// Devuelve la config para mostrar en el renderer: los secretos vienen enmascarados
// (solo un booleano "configurado: true/false"), nunca el valor real.
function getMaskedConfig() {
  const raw = _readRaw();
  const result = {};
  for (const key of PLAIN_KEYS) {
    result[key] = raw[key] !== undefined ? raw[key] : null;
  }
  for (const key of SECRET_KEYS) {
    result[key + '_configurado'] = Boolean(raw[key]);
  }
  return result;
}

function saveSettings(update) {
  const raw = _readRaw();
  for (const key of PLAIN_KEYS) {
    if (update[key] !== undefined) raw[key] = update[key];
  }
  for (const key of SECRET_KEYS) {
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

module.exports = { getDecryptedConfig, getMaskedConfig, saveSettings, SECRET_KEYS, PLAIN_KEYS };
