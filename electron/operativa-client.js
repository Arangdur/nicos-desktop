// Cliente HTTP hacia el sidecar de la Mac de Nicolás, usado por las vistas
// no-Director (Operativa, Enfermero/a de Abate, futuros roles). El token de
// dispositivo vive y se usa acá, en el proceso principal de Electron — nunca
// se le pasa al renderer (contextIsolation), así que ni siquiera un bug de
// XSS en la UI podría filtrarlo.
//
// v0.2.2 -- una misma PC puede tener VARIAS personas vinculadas (ver
// settings-store.getIdentities). `activeUserId` es en memoria, no persiste
// entre reinicios de la app a propósito: cada arranque vuelve a preguntar
// "¿quién sos?" (ver renderer/shared/login.html), coherente con el turno real
// de quien esté sentada/o frente a esa PC en ese momento.
//
// Si la Mac está desconectada, las tareas se guardan en un outbox local (JSON
// plano, sin secretos — solo texto de la tarea) y se reintentan cuando vuelve
// la conexión. Esto es exactamente lo que pidió Nicolás: "Tarea recibida, se
// procesará cuando el ejecutor esté conectado." El LOGIN por PIN funciona
// igual de offline -- ver loginWithPin.
const { app } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const settingsStore = require('./settings-store');

const OUTBOX_PATH = path.join(app.getPath('userData'), 'nicos-outbox.json');
const FETCH_TIMEOUT_MS = 5000;

let activeUserId = null;

function _readOutbox() {
  if (!fs.existsSync(OUTBOX_PATH)) return [];
  try {
    return JSON.parse(fs.readFileSync(OUTBOX_PATH, 'utf-8'));
  } catch (e) {
    return [];
  }
}

function _writeOutbox(items) {
  fs.mkdirSync(path.dirname(OUTBOX_PATH), { recursive: true });
  fs.writeFileSync(OUTBOX_PATH, JSON.stringify(items, null, 2), 'utf-8');
}

function _baseUrl(config) {
  const host = config.MAC_LAN_HOST;
  const port = config.MAC_LAN_PORT || '47500';
  if (!host) return null;
  return `http://${host}:${port}`;
}

async function _fetchWithTimeout(url, opts) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), FETCH_TIMEOUT_MS);
  try {
    return await fetch(url, { ...opts, signal: controller.signal });
  } finally {
    clearTimeout(timer);
  }
}

function _hashPinLocal(pin) {
  return crypto.createHash('sha256').update(pin, 'utf-8').digest('hex');
}

// ---- Identidades: alta, login, listado, olvido -----------------------------

function listIdentities() {
  // Nunca expone token ni pin_hash_local al renderer -- solo lo que hace
  // falta para dibujar el selector "¿Quién sos?".
  return settingsStore.getIdentities().map(({ user_id, display_name, role, turno, device_name }) => ({
    user_id, display_name, role, turno, device_name,
  }));
}

function getActiveIdentity() {
  if (!activeUserId) return null;
  const identity = settingsStore.getIdentities().find((i) => i.user_id === activeUserId);
  if (!identity) return null;
  const { user_id, display_name, role, turno } = identity;
  return { user_id, display_name, role, turno };
}

function logout() {
  activeUserId = null;
}

async function completeAlta({ host, port, code, deviceName, displayName, dni, fechaNacimiento, sexo, pin }) {
  const url = `http://${host}:${port}/api/v1/pairing/complete`;
  const res = await _fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      code, device_name: deviceName, display_name: displayName, dni,
      fecha_nacimiento: fechaNacimiento, sexo, pin,
    }),
  });
  const data = await res.json();
  if (!data.ok) {
    throw new Error(data.error || 'No se pudo completar el alta.');
  }

  settingsStore.saveSettings({
    role: 'operativa', // rol de MÁQUINA ("no es la Mac de Nicolás") -- el rol real de la persona vive en la identidad
    MAC_LAN_HOST: host,
    MAC_LAN_PORT: String(port),
  });
  settingsStore.addIdentity({
    user_id: data.user_id,
    display_name: data.display_name,
    role: data.role,
    turno: data.turno || null,
    device_id: data.device_id,
    device_name: deviceName,
    token: data.token,
    pin_hash_local: _hashPinLocal(pin),
  });
  activeUserId = data.user_id;
  return { ok: true, user_id: data.user_id, role: data.role, display_name: data.display_name };
}

async function loginWithPin(userId, pin) {
  // A propósito 100% local/offline -- ver comentario de cabecera. El PIN no
  // es el mecanismo de seguridad real (ese es el token, ya emitido en el
  // alta) -- es solo "¿esta persona es quien dice ser, en ESTE dispositivo
  // compartido?", y esa pregunta la puede responder el propio dispositivo.
  const identity = settingsStore.getIdentities().find((i) => i.user_id === userId);
  if (!identity) {
    return { ok: false, error: 'No se encontró esa identidad en esta PC.' };
  }
  if (_hashPinLocal(pin) !== identity.pin_hash_local) {
    return { ok: false, error: 'PIN incorrecto.' };
  }
  activeUserId = userId;
  return { ok: true, role: identity.role, display_name: identity.display_name };
}

function forgetIdentity(userId) {
  settingsStore.removeIdentity(userId);
  if (activeUserId === userId) activeUserId = null;
  return { ok: true, remaining: settingsStore.getIdentities().length };
}

// ---- Llamadas autenticadas con el token de la identidad ACTIVA ------------

async function _authedFetch(pathAndQuery, opts = {}) {
  const config = settingsStore.getDecryptedConfig();
  const base = _baseUrl(config);
  const identity = settingsStore.getIdentities().find((i) => i.user_id === activeUserId);
  if (!base || !identity) {
    return { ok: false, offline: true, error: 'No hay una sesión activa vinculada con la Mac.' };
  }
  try {
    const res = await _fetchWithTimeout(`${base}${pathAndQuery}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${identity.token}`,
        ...(opts.headers || {}),
      },
    });
    const data = await res.json();
    return data;
  } catch (e) {
    return { ok: false, offline: true, error: 'La Mac no está disponible en la red en este momento.' };
  }
}

async function submitTask(rawText) {
  const idempotencyKey = crypto.randomUUID();
  const result = await _authedFetch('/api/v1/tasks', {
    method: 'POST',
    body: JSON.stringify({ idempotency_key: idempotencyKey, raw_text: rawText }),
  });

  if (result.offline) {
    const outbox = _readOutbox();
    outbox.push({ idempotency_key: idempotencyKey, raw_text: rawText, queued_at: new Date().toISOString() });
    _writeOutbox(outbox);
    return { ok: true, queued: true, message: 'Tarea recibida. El ejecutor está desconectado. Se procesará cuando vuelva a conectarse.' };
  }
  return result;
}

async function flushOutbox() {
  const outbox = _readOutbox();
  if (outbox.length === 0) return { flushed: 0, remaining: 0 };

  const remaining = [];
  let flushed = 0;
  for (const item of outbox) {
    const result = await _authedFetch('/api/v1/tasks', {
      method: 'POST',
      body: JSON.stringify({ idempotency_key: item.idempotency_key, raw_text: item.raw_text }),
    });
    if (result.offline) {
      remaining.push(item);
    } else {
      flushed += 1;
    }
  }
  _writeOutbox(remaining);
  return { flushed, remaining: remaining.length };
}

async function listTasks() {
  return _authedFetch('/api/v1/tasks');
}

// ---- Recordatorios de turno (Director + Operativa, ambos leen y cargan) --

async function listRecordatorios() {
  return _authedFetch('/api/v1/recordatorios');
}

async function recordatoriosImportar(turnos) {
  return _authedFetch('/api/v1/recordatorios/importar', {
    method: 'POST',
    body: JSON.stringify({ turnos }),
  });
}

async function recordatoriosCompletarTelefono(recordatorioId, telefono) {
  return _authedFetch(`/api/v1/recordatorios/${recordatorioId}/telefono`, {
    method: 'POST',
    body: JSON.stringify({ telefono }),
  });
}

function getOutboxCount() {
  return _readOutbox().length;
}

// ---- Bandeja de mensajes de WhatsApp entrantes (Director + Operativa, con
// bloqueo por rol del lado del servidor para lo que requiere_profesional) --

async function whatsappMensajesList() {
  return _authedFetch('/api/v1/whatsapp/mensajes');
}

async function whatsappMensajeAprobar(mensajeId, textoFinal) {
  return _authedFetch(`/api/v1/whatsapp/mensajes/${mensajeId}/aprobar`, {
    method: 'POST',
    body: JSON.stringify({ texto_final: textoFinal }),
  });
}

async function whatsappMensajeRechazar(mensajeId) {
  return _authedFetch(`/api/v1/whatsapp/mensajes/${mensajeId}/rechazar`, { method: 'POST' });
}

// ---- Enfermería de Abate (Enfermero-only del lado del servidor) -----------

async function abateListResidentes() {
  return _authedFetch('/api/v1/abate/residentes');
}

async function abateGetTratamiento(residenteId) {
  return _authedFetch(`/api/v1/abate/residentes/${residenteId}/tratamiento`);
}

async function abateListAdministracionesHoy(residenteId) {
  return _authedFetch(`/api/v1/abate/residentes/${residenteId}/administraciones`);
}

async function abateRegistrarAdministracion(residenteId, droga, horarioPrevisto) {
  return _authedFetch(`/api/v1/abate/residentes/${residenteId}/administraciones`, {
    method: 'POST',
    body: JSON.stringify({ droga, horario_previsto: horarioPrevisto }),
  });
}

async function abateListNovedades(residenteId) {
  const query = residenteId ? `?residente_id=${residenteId}` : '';
  return _authedFetch(`/api/v1/abate/novedades${query}`);
}

async function abateCreateNovedad(residenteId, categoria, texto) {
  return _authedFetch('/api/v1/abate/novedades', {
    method: 'POST',
    body: JSON.stringify({ residente_id: residenteId, categoria, texto }),
  });
}

module.exports = {
  completeAlta, loginWithPin, listIdentities, getActiveIdentity, logout, forgetIdentity,
  submitTask, flushOutbox, listTasks, getOutboxCount,
  abateListResidentes, abateGetTratamiento, abateListAdministracionesHoy,
  abateRegistrarAdministracion, abateListNovedades, abateCreateNovedad,
  listRecordatorios, recordatoriosImportar, recordatoriosCompletarTelefono,
  whatsappMensajesList, whatsappMensajeAprobar, whatsappMensajeRechazar,
};
