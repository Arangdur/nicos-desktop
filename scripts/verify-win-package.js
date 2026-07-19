#!/usr/bin/env node
// Confirma que el paquete Windows (Operativa) NO embebe el sidecar Python ni
// nada que dependa de él -- Windows nunca es Director (esa restricción ya
// existe en tiempo de ejecución vía main.js/_bootAndLoad, "solo el rol
// Director levanta el sidecar Python"), así que el paquete de Marianela no
// tiene ningún motivo para cargar ese código en absoluto. Antes de rc10,
// build.win.extraResources copiaba el mismo sidecar/dist/nicos-sidecar.exe
// y policies/ que Mac -- este script existe para que ese error de
// configuración no pueda volver a colarse sin que algo falle fuerte.
//
// Uso: node scripts/verify-win-package.js <ruta a resources/ del paquete>
//   (electron-builder con target win/nsis deja el staging sin empaquetar en
//   dist/win-unpacked/resources/ antes de generar el instalador -- también
//   sirve apuntarlo directo ahí si el .exe ya se generó y se lo quiere
//   inspeccionar por separado extrayendo el instalador NSIS.)
const fs = require('fs');
const path = require('path');

const FORBIDDEN_NAMES = [
  'sidecar',
  'nicos-sidecar',
  'nicos-sidecar.exe',
  'policies',
  'risk_policy.yaml',
  'provider_matrix.json',
  'migrations',
  'centro_mando_adapter.py',
  'registrar_movimiento.py',
];

function walk(dir, found) {
  for (const entry of fs.readdirSync(dir, { withFileTypes: true })) {
    const full = path.join(dir, entry.name);
    if (FORBIDDEN_NAMES.includes(entry.name)) {
      found.push(full);
      if (entry.isDirectory()) continue; // ya se marcó el directorio entero, no hace falta bajar
    }
    if (entry.isDirectory()) {
      walk(full, found);
    }
  }
}

// El nombre de la carpeta que deja electron-builder --dir depende de la
// arquitectura del build ("win-unpacked", "win-arm64-unpacked",
// "win-ia32-unpacked", etc.) -- en vez de exigir la ruta exacta, se busca
// dentro de dist/ cualquier carpeta que matchee win*unpacked.
function findUnpackedResources(distDir) {
  if (!fs.existsSync(distDir)) return null;
  for (const entry of fs.readdirSync(distDir, { withFileTypes: true })) {
    if (entry.isDirectory() && /^win.*unpacked$/.test(entry.name)) {
      const resources = path.join(distDir, entry.name, 'resources');
      if (fs.existsSync(resources)) return resources;
    }
  }
  return null;
}

const arg = process.argv[2] || 'dist';
let target = arg;
if (!fs.existsSync(target)) {
  console.error(`[verify-win-package] no existe: ${target}`);
  process.exit(2);
}
if (fs.statSync(target).isDirectory() && path.basename(target) !== 'resources') {
  const found = findUnpackedResources(target);
  if (!found) {
    console.error(`[verify-win-package] no se encontró ninguna carpeta win*unpacked/resources dentro de ${target}`);
    console.error('¿Se corrió "electron-builder --win --dir" antes de este script?');
    process.exit(2);
  }
  target = found;
}

const found = [];
walk(target, found);

if (found.length > 0) {
  console.error('[verify-win-package] FALLÓ -- el paquete Windows contiene contenido que no debería llevar:');
  for (const f of found) console.error(`  - ${f}`);
  console.error('El paquete Operativa/Windows no debe incluir el sidecar Python, migraciones,');
  console.error('provider_matrix.json, ni policies/ -- ver package.json build.win.extraResources.');
  process.exit(1);
}

console.log(`[verify-win-package] OK -- ${target} no contiene sidecar, migraciones, provider_matrix ni policies.`);
