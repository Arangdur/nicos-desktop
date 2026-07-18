#!/usr/bin/env bash
# Corre una instancia de NicOS Desktop en rol Director, TOTALMENTE aislada de
# los datos productivos: configuración de Electron (userData) Y datos del
# sidecar (nicos.db, foto_financiera de prueba, REGISTRO_ENTRADA, ledger de
# operation_id) en una carpeta temporal propia, nunca la real.
#
# Esto existe porque la primera prueba local (18/7/2026) encontró que aislar
# solo el userData de Electron NO alcanza: el sidecar Python lee/escribe
# nicos.db por una ruta fija relativa a su propio código, sin relación con
# qué instancia de Electron lo lanzó. Sin este script, dos instancias de
# prueba corriendo "aisladas" en apariencia terminaban compartiendo la misma
# base de datos real del proyecto.
#
# Antes de arrancar Electron, el script fuerza TODAS las rutas del sidecar a
# una carpeta de prueba y verifica -- no asume -- que ninguna terminó
# apuntando a una ubicación productiva. Si algo falla la verificación, el
# script aborta y no arranca nada.
#
# El flujo financiero productivo queda estructuralmente deshabilitado: como
# NICOS_CFO_DIR/NICOS_REGISTRO_ENTRADA/NICOS_OPERATION_LEDGER apuntan a una
# carpeta de prueba, ninguna aprobación que se ejecute durante esta sesión
# puede tocar foto_financiera_*.md real, aunque el flujo de tareas (pairing,
# aprobar, rechazar, etc.) funcione con normalidad -- es justamente lo que
# hace falta para probarlo.
#
# Uso:
#   bash scripts/run_test_director_mac.sh
#
# Variable opcional NICOS_TEST_ROOT para elegir dónde viven los datos de
# prueba (por defecto, una carpeta fija bajo /tmp, reutilizada entre corridas
# para no perder el estado de una sesión de pruebas a la siguiente).
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
TEST_ROOT="${NICOS_TEST_ROOT:-/tmp/nicos-local-test}"

DIRECTOR_USERDATA="$TEST_ROOT/electron-director"
CFO_DIR="$TEST_ROOT/cfo"
CENTRO_DIR="$TEST_ROOT/centro"
JARVIS_DIR="$TEST_ROOT/jarvis"
DB_PATH="$TEST_ROOT/nicos.db"
REGISTRO_ENTRADA="$CENTRO_DIR/REGISTRO_ENTRADA.md"
OPERATION_LEDGER="$CENTRO_DIR/OPERATION_IDS_PROCESADOS.txt"

mkdir -p "$CFO_DIR" "$CENTRO_DIR" "$JARVIS_DIR" "$DIRECTOR_USERDATA"

if [ ! -f "$CFO_DIR/foto_financiera_testmes.md" ]; then
  cat > "$CFO_DIR/foto_financiera_testmes.md" <<'EOF'
# Foto financiera de PRUEBA -- generada por run_test_director_mac.sh, nunca es el archivo real.

## 6. MOVIMIENTOS DEL MES

| Fecha | Concepto | Monto | Tipo |
|---|---|---|---|

---
EOF
fi

export NICOS_DB_PATH="$DB_PATH"
export NICOS_CFO_DIR="$CFO_DIR"
export NICOS_REGISTRO_ENTRADA="$REGISTRO_ENTRADA"
export NICOS_OPERATION_LEDGER="$OPERATION_LEDGER"
export NICOS_JARVIS_TRABAJO="$JARVIS_DIR"

# ---- Guardas de seguridad: abortar si CUALQUIERA de las 4 rutas pedidas por
# Nicolás (más NICOS_JARVIS_TRABAJO, mismo riesgo -- cfo-vivo-resumen.json se
# sobrescribiría en el real si no se redirige) queda sin definir o resuelve a
# una ubicación productiva conocida. No confía en que las asignaciones de
# arriba salieron bien -- las vuelve a leer del entorno y las verifica. ----

PRODUCTION_PATTERNS=(
  "/Claude/Projects/CFO y Decisiones Estrategicas"
  "/Claude/Projects/Centro de Mando"
  "$SCRIPT_DIR/sidecar/nicos.db"
  "/trading_bot/jarvis-trabajo"
)

_abort() {
  echo "" >&2
  echo "❌ ABORTADO -- $1" >&2
  echo "   No se arrancó ningún proceso." >&2
  exit 1
}

_check_safe() {
  local var_name="$1"
  local var_value="${!var_name:-}"
  if [ -z "$var_value" ]; then
    _abort "$var_name no está definida."
  fi
  local resolved
  resolved="$(python3 -c "import os,sys; print(os.path.realpath(sys.argv[1]))" "$var_value" 2>/dev/null || echo "$var_value")"
  for pattern in "${PRODUCTION_PATTERNS[@]}"; do
    case "$resolved" in
      *"$pattern"*)
        _abort "$var_name ('$var_value') resuelve dentro de una ubicación productiva conocida ('$pattern'). Un script de prueba nunca debe apuntar ahí."
        ;;
    esac
  done
  echo "  ✓ $var_name = $var_value"
}

echo "Verificando aislamiento de rutas financieras..."
_check_safe "NICOS_DB_PATH"
_check_safe "NICOS_CFO_DIR"
_check_safe "NICOS_REGISTRO_ENTRADA"
_check_safe "NICOS_OPERATION_LEDGER"
_check_safe "NICOS_JARVIS_TRABAJO"
echo "✅ Ninguna ruta apunta a producción -- el flujo financiero productivo queda deshabilitado para esta sesión."
echo ""
echo "userData de Electron: $DIRECTOR_USERDATA"
echo ""

cd "$SCRIPT_DIR"
exec npx electron . --user-data-dir="$DIRECTOR_USERDATA"
