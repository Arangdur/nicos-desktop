// Cliente HTTP hacia el sidecar de la Mac de Nicolás, usado SOLO por la vista
// Operativa. El token de dispositivo vive y se usa acá, en el proceso principal
// de Electron — nunca se le pasa al renderer (contextIsolation), así que ni
// siquiera un bug de XSS en la UI podría filtrarlo.
//
// Si la Mac está desconectada, las tareas se guardan en un outbox local (JSON
// plano, sin secretos — solo texto de la tarea) y se reintentan cuando vuelve
// la conexión. Esto es exactamente lo que pidió Nicolás: "Tarea recibida, se
// procesará cuando el ejecutor esté conectado."
const { app } = require('electron');
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');

const settingsStore = require('./settings-store');

const OUTBOX_PATH = path.join(app.getPath('userData'), 'nicos-outbox.json');
const FETCH_TIMEOUT_MS = 5000;

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

async function pairWithMac(host, port, code, deviceName) {
  const url = `http://${host}:${port}/api/v1/pairing/complete`;
  const res = await _fetchWithTimeout(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ code, device_name: deviceName }),
  });
  const data = await res.json();
  if (!data.ok) {
    throw new Error(data.error || 'No se pudo vincular.');
  }
  settingsStore.saveSettings({
    role: 'operativa',
    MAC_LAN_HOST: host,
    MAC_LAN_PORT: String(port),
    PAIRED_DEVICE_ID: data.device_id,
    PAIRED_DEVICE_NAME: deviceName,
    PAIRED_DEVICE_TOKEN: data.token,
  });
  return { ok: true };
}

async function _authedFetch(pathAndQuery, opts = {}) {
  const config = settingsStore.getDecryptedConfig();
  const base = _baseUrl(config);
  if (!base || !config.PAIRED_DEVICE_TOKEN) {
    return { ok: false, offline: true, error: 'No hay vinculación con la Mac configurada.' };
  }
  try {
    const res = await _fetchWithTimeout(`${base}${pathAndQuery}`, {
      ...opts,
      headers: {
        'Content-Type': 'application/json',
        Authorization: `Bearer ${config.PAIRED_DEVICE_TOKEN}`,
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

async function listMessages() {
  return _authedFetch('/api/v1/whatsapp/messages');
}

async function updateMessage(row, updates) {
  return _authedFetch('/api/v1/whatsapp/messages/update', {
    method: 'POST',
    body: JSON.stringify({ row, updates }),
  });
}

function getOutboxCount() {
  return _readOutbox().length;
}

module.exports = {
  pairWithMac, submitTask, flushOutbox, listTasks, getOutboxCount, listMessages, updateMessage,
};
