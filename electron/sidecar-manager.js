// Lanza y gestiona el subproceso del sidecar Python (mismo patrón de "server local
// + puerto elegido por el SO" que ya se probó standalone con curl en esta sesión).
//
// En desarrollo: corre sidecar/server.py con el intérprete del venv del propio repo.
// Empaquetado: corre el binario ya compilado por PyInstaller, embebido como extraResource
// (ver package.json -> build.extraResources) — no depende de que la máquina destino
// tenga Python instalado.
const { app } = require('electron');
const { spawn } = require('child_process');
const path = require('path');
const fs = require('fs');

let sidecarProcess = null;
let sidecarPort = null;
let readyPromise = null;

function _resolveCommand() {
  const isPackaged = app.isPackaged;
  if (isPackaged) {
    const binName = process.platform === 'win32' ? 'nicos-sidecar.exe' : 'nicos-sidecar';
    const bin = path.join(process.resourcesPath, 'sidecar', binName);
    return { command: bin, args: [] };
  }
  const repoRoot = path.join(__dirname, '..');
  const venvPythonName = process.platform === 'win32'
    ? path.join('Scripts', 'python.exe')
    : path.join('bin', 'python3');
  const venvPython = path.join(repoRoot, 'sidecar', '.venv', venvPythonName);
  const python = fs.existsSync(venvPython) ? venvPython : 'python3';
  return { command: python, args: [path.join(repoRoot, 'sidecar', 'server.py')] };
}

function startSidecar(envOverrides) {
  if (readyPromise) return readyPromise;

  readyPromise = new Promise((resolve, reject) => {
    const { command, args } = _resolveCommand();
    const cwd = app.isPackaged
      ? path.join(process.resourcesPath, 'sidecar')
      : path.join(__dirname, '..', 'sidecar');

    sidecarProcess = spawn(command, args, {
      cwd,
      env: { ...process.env, ...envOverrides },
    });

    let resolved = false;
    const timeout = setTimeout(() => {
      if (!resolved) reject(new Error('El sidecar no arrancó a tiempo (10s).'));
    }, 10000);

    sidecarProcess.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      const match = text.match(/NICOS_SIDECAR_PORT=(\d+)/);
      if (match && !resolved) {
        sidecarPort = parseInt(match[1], 10);
        resolved = true;
        clearTimeout(timeout);
        resolve(sidecarPort);
      }
      process.stdout.write(`[sidecar] ${text}`);
    });

    sidecarProcess.stderr.on('data', (chunk) => {
      process.stderr.write(`[sidecar] ${chunk.toString()}`);
    });

    sidecarProcess.on('exit', (code) => {
      console.log(`[sidecar] proceso terminado, código ${code}`);
      sidecarProcess = null;
      sidecarPort = null;
      readyPromise = null;
    });

    sidecarProcess.on('error', (err) => {
      if (!resolved) {
        clearTimeout(timeout);
        reject(err);
      }
    });
  });

  return readyPromise;
}

function stopSidecar() {
  if (sidecarProcess) {
    sidecarProcess.kill();
    sidecarProcess = null;
    sidecarPort = null;
    readyPromise = null;
  }
}

// Reinicia con nuevas variables de entorno (ej. después de guardar Ajustes con API keys nuevas).
async function restartSidecar(envOverrides) {
  stopSidecar();
  return startSidecar(envOverrides);
}

function getPort() {
  return sidecarPort;
}

module.exports = { startSidecar, stopSidecar, restartSidecar, getPort };
