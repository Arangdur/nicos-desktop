#!/usr/bin/env node
// Genera build/build-info.json en el momento de compilar (v0.2.1-rc7) -- NO
// en el commit, porque el commit SHA y la fecha de build tienen que reflejar
// el estado exacto en el que se armó ESE paquete puntual, no cuándo se
// escribió este script. Se llama desde `npm run dist:mac` / `dist:win`,
// antes de electron-builder -- build-info.json queda embebido en el paquete
// (ver package.json -> build.files, ya incluye "build/**/*") y es lo que
// "Acerca de NicOS" muestra: con qué código corre exactamente una
// instalación real, sin depender de que alguien se acuerde qué tag instaló.
//
// Deliberadamente NO incluye plataforma/arquitectura/versión de Electron --
// esos se leen en tiempo de ejecución (process.platform/arch/versions.electron
// en main.js), que es más preciso que congelarlos acá (y sigue siendo
// correcto aunque el mismo build-info.json se use por error para inspeccionar
// un paquete corriendo en otra arquitectura).
//
// Uso: node scripts/generate-build-info.js
const fs = require('fs');
const path = require('path');
const crypto = require('crypto');
const { execSync } = require('child_process');

const repoRoot = path.join(__dirname, '..');
const pkg = JSON.parse(fs.readFileSync(path.join(repoRoot, 'package.json'), 'utf-8'));

function sh(cmd, fallback) {
  try {
    return execSync(cmd, { cwd: repoRoot, encoding: 'utf-8' }).trim();
  } catch (e) {
    return fallback;
  }
}

function sha256File(filePath) {
  try {
    const buf = fs.readFileSync(filePath);
    return crypto.createHash('sha256').update(buf).digest('hex');
  } catch (e) {
    return null;
  }
}

const policyPath = path.join(repoRoot, 'policies', 'risk_policy.yaml');

const buildInfo = {
  version: pkg.version,
  commit_sha: sh('git rev-parse HEAD', 'desconocido (sin git en el entorno de build)'),
  commit_sha_short: sh('git rev-parse --short HEAD', 'desconocido'),
  // Un build con cambios sin commitear no debería pasar a producción -- si
  // esto da `true` en un paquete real, algo se saltó el proceso normal.
  git_dirty: sh('git status --porcelain', '') !== '',
  build_date: new Date().toISOString(),
  risk_policy_sha256: sha256File(policyPath),
};

if (buildInfo.risk_policy_sha256 === null) {
  console.error('[build-info] ADVERTENCIA: no se pudo leer policies/risk_policy.yaml para el hash.');
}

const outDir = path.join(repoRoot, 'build');
fs.mkdirSync(outDir, { recursive: true });
const outPath = path.join(outDir, 'build-info.json');
fs.writeFileSync(outPath, JSON.stringify(buildInfo, null, 2) + '\n');
console.log(`[build-info] generado en ${outPath}:`);
console.log(JSON.stringify(buildInfo, null, 2));
