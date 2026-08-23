// v0.2.7 (23/08) -- hallazgo real instalando el primer .dmg en la propia
// Mac de Nicolás: sin ninguna firma (ni siquiera ad-hoc), Apple Silicon
// rechaza el ejecutable directo ("AMFI: no CMS blob") y macOS lo manda
// solo a la Papelera cada vez que se intenta abrir -- no es malware, es
// que no hay firma en absoluto. No hay certificado de Apple Developer
// (cuesta u$s99/año, es cuenta de Nicolás, no algo que se resuelva acá) --
// electron-builder no firma solo cuando no encuentra ninguna identidad
// válida en el llavero, y no acepta "-" (ad-hoc) como nombre de identidad.
// Este hook corre el `codesign` real después del empaquetado -- ad-hoc
// alcanza para que el sistema lo deje ejecutar (sigue mostrando
// "desarrollador no identificado" la primera vez, pero ya no lo borra).
const { execFileSync } = require('child_process');
const path = require('path');

exports.default = async function afterSign(context) {
  if (context.electronPlatformName !== 'darwin') return;
  const appName = context.packager.appInfo.productFilename;
  const appPath = path.join(context.appOutDir, `${appName}.app`);
  console.log(`[afterSign] firmando ad-hoc: ${appPath}`);
  execFileSync('codesign', ['--force', '--deep', '--sign', '-', appPath], { stdio: 'inherit' });
};
