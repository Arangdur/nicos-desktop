// Almacenamiento de configuración — separado por perfil de rol, a propósito.
//
// Perfil Director (Nicolás, Mac): TODOS los secretos de IA/Google viven acá,
// cifrados con safeStorage (Keychain). Es el único perfil con esta capacidad.
//
// Perfil "operativa" a nivel de MÁQUINA (Windows/PC que no es la de Nicolás —
// puede ser Marianela, o la PC de enfermería de Abate compartida entre varias
// personas): NUNCA guarda ninguna API key ni credencial de Google — solo
// identidades de personas vinculadas (ver IDENTITIES_JSON) y la dirección LAN
// de la Mac. No hay forma de que un secreto termine en esta máquina porque el
// código ni siquiera tiene un campo para escribirlo (ver settings-panel-operativa.js).
//
// v0.2.2 -- antes había UN token por instalación (PAIRED_DEVICE_TOKEN). Ahora
// una misma PC puede tener VARIAS personas vinculadas (ej. Carlos y María
// comparten la PC de enfermería, con continuidad de turno) -- cada una con su
// propio código, su propio token, su propia ficha. IDENTITIES_JSON reemplaza
// los campos PAIRED_DEVICE_* singulares. Proyecto pre-lanzamiento (todavía sin
// datos productivos reales de Operativa) -- es un cambio limpio, no una
// migración de settings viejos.
const { app, safeStorage } = require('electron');
const fs = require('fs');
const path = require('path');

const SETTINGS_PATH = path.join(app.getPath('userData'), 'nicos-settings.json');

const DIRECTOR_SECRET_KEYS = [
  'ANTHROPIC_API_KEY',
  'OPENAI_API_KEY',
  'TWILIO_AUTH_TOKEN',
  'DRAPP_API_KEY',
  'GMAIL_CLIENT_SECRET',
  'GMAIL_REFRESH_TOKEN_CONSULTORIO',
  'GMAIL_REFRESH_TOKEN_ABATE',
];
const DIRECTOR_PLAIN_KEYS = [
  'ANTHROPIC_MODEL', 'OPENAI_MODEL', 'TAILSCALE_IP',
  'TWILIO_ACCOUNT_SID', 'TWILIO_WHATSAPP_FROM',
  'DRAPP_TEAM_ID',
  'DRAPP_RESOURCE_ID',
  'DRAPP_SERVICE_KEY_MEDICINA_GENERAL',
  'TWILIO_WEBHOOK_BASE_URL',
  'FACTURACION_TELEFONOS_AUTORIZADOS',
  'CONSULTORIO_WHATSAPP_NUMERO',
  'GMAIL_CLIENT_ID',
];

// IDENTITIES_JSON contiene los tokens de TODAS las personas vinculadas a esta
// PC -- por eso se cifra igual que las API keys, aunque conceptualmente sea
// "de otro tipo" (un array serializado, no un secreto simple).
const OPERATIVA_SECRET_KEYS = ['IDENTITIES_JSON'];
const OPERATIVA_PLAIN_KEYS = ['MAC_LAN_HOST', 'MAC_LAN_PORT'];

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
  const role = update.role !== undefined ? update.role : (raw.role || 'director');
  const { secret, plain } = _keysForRole(role);

  // v0.2.1: antes esto ignoraba en silencio cualquier clave que no perteneciera
  // al rol actual (ej. Operativa intentando guardar ANTHROPIC_API_KEY). Ahora
  // rechaza explícitamente para que quede auditable en vez de invisible — es
  // la restricción de rol aplicada en el proceso principal, no solo en la UI.
  const allowedKeys = new Set([...COMMON_PLAIN_KEYS, ...plain, ...secret]);
  for (const key of Object.keys(update)) {
    if (!allowedKeys.has(key)) {
      throw new Error(`La clave "${key}" no pertenece al rol "${role}" — guardado rechazado.`);
    }
  }

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

// ---- Identidades (v0.2.2) -------------------------------------------------
// Cada identidad: { user_id, display_name, role, turno, device_id, device_name,
// token, pin_hash_local }. `token` y `pin_hash_local` viajan dentro del blob
// cifrado IDENTITIES_JSON -- nunca en texto plano en disco. `pin_hash_local`
// es un hash SHA-256 calculado en el cliente a partir del PIN que la persona
// tipeó en su alta -- el login por PIN se verifica OFFLINE, a propósito (ver
// operativa-client.loginWithPin): si tuviera que consultar a la Mac cada vez,
// alguien legítimamente vinculado no podría ni abrir la app cuando la Mac de
// Nicolás está apagada/dormida, rompiendo el diseño de "cola local si no hay
// conexión" que ya existía para el envío de tareas.

function getIdentities() {
  const config = getDecryptedConfig();
  if (!config.IDENTITIES_JSON) return [];
  try {
    return JSON.parse(config.IDENTITIES_JSON);
  } catch (e) {
    console.error('[settings-store] IDENTITIES_JSON corrupto:', e);
    return [];
  }
}

function _setIdentities(list) {
  saveSettings({ role: 'operativa', IDENTITIES_JSON: JSON.stringify(list) });
}

function addIdentity(identity) {
  const list = getIdentities().filter((i) => i.user_id !== identity.user_id);
  list.push(identity);
  _setIdentities(list);
}

function removeIdentity(userId) {
  const list = getIdentities().filter((i) => i.user_id !== userId);
  _setIdentities(list);
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
  getIdentities, addIdentity, removeIdentity,
  DIRECTOR_SECRET_KEYS, DIRECTOR_PLAIN_KEYS, OPERATIVA_SECRET_KEYS, OPERATIVA_PLAIN_KEYS,
};
