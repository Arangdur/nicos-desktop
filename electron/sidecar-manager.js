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
      // v0.2.12 (01/09) -- hallazgo real en producción: el binario de
      // PyInstaller (onefile) es un bootloader que spawnea un proceso HIJO
      // propio -- el que de verdad abre el puerto de red. `.kill()` en
      // sidecarProcess solo mataba el bootloader; el hijo quedaba huérfano
      // (reparentado a launchd) y seguía vivo y escuchando en el puerto
      // viejo -- confirmado: un zombie del 27/08 siguió sirviendo pedidos
      // reales de Marianela durante 5 DÍAS con una API key vieja, mientras
      // el proceso "nuevo" ni siquiera lograba bindear el puerto de red
      // (ya estaba tomado) y quedaba solo en un puerto local efímero sin
      // que nadie lo notara. `detached: true` en POSIX hace que el
      // bootloader sea líder de su propio grupo de procesos -- cualquier
      // hijo que spawnee hereda ese mismo grupo, así que matar el GRUPO
      // completo (ver stopSidecar) mata a los dos.
      detached: process.platform !== 'win32',
    });

    let resolved = false;
    // v0.2.7 (23/08) -- hallazgo real: la app empaquetada mostraba "sidecar
    // no disponible" en el primer arranque, aunque el sidecar SÍ estaba
    // sano (confirmado pegándole directo al puerto real mientras la UI
    // decía lo contrario). Dos causas reales, las dos corregidas acá:
    // (1) el regex solo miraba el ÚLTIMO chunk de stdout -- si Node parte
    // "NICOS_SIDECAR_PORT=NNNN" justo en el medio entre dos eventos 'data'
    // (Node no garantiza que un chunk sea una línea completa), nunca
    // matcheaba ninguno de los dos. Ahora se acumula todo el stdout visto
    // y se busca ahí. (2) 10s podía quedar justo en un primer arranque
    // empaquetado (migraciones + backup + verificación de la firma nueva)
    // -- se sube a 20s de margen.
    let stdoutBuffer = '';
    const timeout = setTimeout(() => {
      if (resolved) return;
      // v0.2.7 (23/08) -- hallazgo real, causa de fondo: sin esto,
      // `readyPromise` quedaba cacheado como RECHAZADO para siempre --
      // ningún reintento ("Intentar nuevamente" en la UI) volvía a probar
      // de verdad, aunque el proceso del sidecar siguiera vivo y sano (se
      // confirmó pegándole directo al puerto real mientras la UI seguía
      // diciendo "no disponible"). Se mata el proceso que no llegó a
      // avisar su puerto a tiempo (para no dejarlo huérfano ocupando el
      // puerto) y se limpia el estado -- así el próximo llamado arranca
      // uno nuevo de verdad en vez de reusar esta promesa ya perdida.
      const proc = sidecarProcess;
      sidecarProcess = null;
      readyPromise = null;
      if (proc) proc.kill();
      reject(new Error('El sidecar no arrancó a tiempo (20s).'));
    }, 20000);

    sidecarProcess.stdout.on('data', (chunk) => {
      const text = chunk.toString();
      stdoutBuffer += text;
      const match = stdoutBuffer.match(/NICOS_SIDECAR_PORT=(\d+)/);
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

// Devuelve una promesa que se resuelve cuando el proceso realmente terminó
// (evento 'exit'), no apenas se le mandó la señal -- SIGTERM no es
// instantáneo, y arrancar el reemplazo antes de que el viejo soltara el
// puerto es exactamente lo que dejó vivo al zombie del 27/08 (ver comentario
// en startSidecar). Escala a SIGKILL si a los 3s segundos no murió solo.
function stopSidecar() {
  if (!sidecarProcess) return Promise.resolve();
  const proc = sidecarProcess;
  const pid = proc.pid;
  sidecarProcess = null;
  sidecarPort = null;
  readyPromise = null;

  return new Promise((resolve) => {
    let done = false;
    proc.once('exit', () => {
      done = true;
      resolve();
    });
    const killGroup = (signal) => {
      try {
        // grupo completo (ver detached:true en startSidecar) -- pid negativo
        // en POSIX es "todo el grupo", no solo el proceso. En Windows no hay
        // grupos así, matamos el único proceso que Node conoce.
        process.kill(process.platform === 'win32' ? pid : -pid, signal);
      } catch (err) {
        // ESRCH = ya estaba muerto, no es un error real acá.
        if (err.code !== 'ESRCH') console.error('[sidecar] error matando proceso viejo:', err);
        if (!done) { done = true; resolve(); }
      }
    };
    killGroup('SIGTERM');
    setTimeout(() => { if (!done) killGroup('SIGKILL'); }, 3000);
  });
}

// Reinicia con nuevas variables de entorno (ej. después de guardar Ajustes con API keys nuevas).
async function restartSidecar(envOverrides) {
  await stopSidecar();
  return startSidecar(envOverrides);
}

function getPort() {
  return sidecarPort;
}

module.exports = { startSidecar, stopSidecar, restartSidecar, getPort };
