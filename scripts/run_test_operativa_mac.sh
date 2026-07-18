#!/usr/bin/env bash
# Corre una segunda instancia de NicOS Desktop en rol Operativa, simulando la
# PC de Marianela en esta misma Mac -- userData completamente separado del
# de run_test_director_mac.sh (settings, token de dispositivo, outbox).
#
# A diferencia del script Director, esta vista nunca toca nicos.db ni
# foto_financiera_*.md directamente (no arranca sidecar propio -- el rol
# Operativa nunca lo hace, ver electron/main.js) -- todo pasa por HTTP contra
# el sidecar de la instancia Director, así que no hace falta redirigir rutas
# financieras acá; no hay ninguna que redirigir.
#
# Uso:
#   bash scripts/run_test_operativa_mac.sh
#
# Variable opcional NICOS_TEST_ROOT (misma que usa run_test_director_mac.sh)
# para elegir dónde vive el userData de esta instancia.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="${NICOS_TEST_ROOT:-/tmp/nicos-local-test}"
OPERATIVA_USERDATA="$TEST_ROOT/electron-operativa"

mkdir -p "$OPERATIVA_USERDATA"

echo "userData de Electron (Operativa): $OPERATIVA_USERDATA"
echo ""

cd "$SCRIPT_DIR"
exec npx electron . --user-data-dir="$OPERATIVA_USERDATA"
