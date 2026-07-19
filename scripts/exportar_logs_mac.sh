#!/usr/bin/env bash
# Junta en una carpeta con fecha lo necesario para diagnosticar un problema
# durante la prueba física.
#
# Por defecto genera un REPORTE SANITIZADO. Explícitamente NO exporta (por
# defecto): tokens/secretos, texto de tareas, montos/resultados, variables de
# entorno, excepciones sin redactar, ni rutas locales con el nombre de
# usuario. Ver el detalle de qué se excluye en cada sección más abajo.
#
# La copia ÍNTEGRA de nicos.db + el log crudo de npm start (que SÍ pueden
# tener texto de tareas, importes, resultados, o tracebacks con detalle
# operativo) requieren el flag explícito --include-database, y el script
# avisa antes de incluirlos.
#
# Uso:
#   cd "NicOS Desktop"
#   bash scripts/exportar_logs_mac.sh                    # solo reporte sanitizado
#   bash scripts/exportar_logs_mac.sh --include-database  # + nicos.db + log crudo
#
# No toca ningún archivo real de Centro de Mando ni de CFO y Decisiones
# Estrategicas -- solo lee, nunca escribe fuera de la carpeta que genera.
set -euo pipefail

INCLUDE_DB=false
for arg in "$@"; do
  if [ "$arg" = "--include-database" ]; then
    INCLUDE_DB=true
  fi
done

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$HOME/Desktop/nicos-logs-mac-$(date +%Y%m%d-%H%M%S)"
mkdir -p "$OUT_DIR"

echo "Exportando a: $OUT_DIR"

# --- Metadata de build (v0.2.1-rc7) -- busca primero en el checkout de
# desarrollo (build/build-info.json, generado por "npm run dist:mac"), y si
# no está, en la app instalada real (Contents/Resources/build-info.json,
# donde queda como extraResource plano, fuera del asar). Con qué código
# corre exactamente esto -- útil para saber si un problema reportado
# corresponde a la versión esperada antes de perder tiempo diagnosticando. ---
{
  echo "=== build-info ==="
  if [ -f "$SCRIPT_DIR/build/build-info.json" ]; then
    cat "$SCRIPT_DIR/build/build-info.json"
  elif [ -f "/Applications/NicOS Desktop.app/Contents/Resources/build-info.json" ]; then
    cat "/Applications/NicOS Desktop.app/Contents/Resources/build-info.json"
  else
    echo "(no se encontró build-info.json -- ¿se corrió 'npm run dist:mac' o está instalada la app?)"
  fi
} > "$OUT_DIR/build_info.txt"

# --- Tailscale ---
{
  echo "=== tailscale status ==="
  tailscale status 2>&1 || echo "(tailscale no disponible o no logueado)"
  echo
  echo "=== tailscale ip -4 ==="
  tailscale ip -4 2>&1 || echo "(sin IP asignada)"
} > "$OUT_DIR/tailscale.txt"

# --- Puerto de red del sidecar ---
# Se recorta la columna USER de lsof a propósito -- por defecto muestra el
# nombre de usuario de macOS que corre el proceso, que no hace falta para
# este diagnóstico (alcanza con COMMAND/PID/TYPE/NAME).
{
  echo "=== proceso escuchando en :47500 (sin columna USER) ==="
  lsof -iTCP:47500 -sTCP:LISTEN -P 2>&1 | awk '{print $1, $2, $5, $9}' || echo "(nada escuchando en 47500)"
} > "$OUT_DIR/puerto_red.txt"

# --- Reporte SANITIZADO de nicos.db (por defecto) ---
DB_PATH="$SCRIPT_DIR/sidecar/nicos.db"
if [ -f "$DB_PATH" ] && command -v sqlite3 >/dev/null 2>&1; then
  # Cada consulta corre independiente -- si el esquema local está desactualizado
  # (falta una migración), esa sección se anota como no disponible en vez de
  # cortar todo el reporte.
  run_query() {
    local titulo="$1" sql="$2"
    echo "=== $titulo ==="
    sqlite3 "$DB_PATH" "$sql" 2>&1 || echo "(no disponible -- ¿faltan migraciones por aplicar en esta base?)"
    echo
  }
  {
    run_query "conteo de tareas por estado" "SELECT state, COUNT(*) FROM tasks GROUP BY state ORDER BY state;"
    run_query "últimas 30 tareas: solo id/estado/timestamps (sin texto ni montos)" \
      "SELECT task_id, state, task_revision, created_at, updated_at FROM tasks ORDER BY created_at DESC LIMIT 30;"
    run_query "execution_attempts: solo id/estado/timestamps (sin result_json ni error_message)" \
      "SELECT execution_id, task_id, status, executor, started_at, finished_at FROM execution_attempts ORDER BY started_at DESC LIMIT 30;"
    run_query "dispositivos pareados (sin tokens)" \
      "SELECT device_id, device_name, user_id, paired_at, revoked_at FROM devices ORDER BY paired_at DESC;"
    run_query "migraciones aplicadas" "SELECT filename, applied_at FROM _migrations ORDER BY applied_at;"
  } > "$OUT_DIR/reporte_sanitizado.txt"

  if [ "$INCLUDE_DB" = true ]; then
    echo
    echo "⚠️  ADVERTENCIA: copiando nicos.db COMPLETA -- puede contener texto de"
    echo "   tareas, importes, resultados financieros y otros datos operativos."
    echo "   Compartir este archivo con criterio."
    cp "$DB_PATH" "$OUT_DIR/nicos.db"
  fi
else
  echo "(no se encontró la base de datos local o falta sqlite3)" > "$OUT_DIR/nicos_db_no_disponible.txt"
fi

# --- Log de npm start (solo con --include-database): puede tener tracebacks
# de Python sin redactar, o cualquier cosa que se haya impreso en esa
# terminal durante la sesión -- no se puede garantizar que esté limpio, así
# que no se copia por defecto. ---
if [ "$INCLUDE_DB" = true ]; then
  LATEST_LOG=$(ls -t "$HOME"/Desktop/nicos-mac-*.log 2>/dev/null | head -1 || true)
  if [ -n "${LATEST_LOG:-}" ]; then
    echo "⚠️  Copiando el log crudo de npm start -- revisar antes de compartir."
    cp "$LATEST_LOG" "$OUT_DIR/npm_start.log"
  fi
fi

echo "Listo. Carpeta: $OUT_DIR"
if [ "$INCLUDE_DB" = false ]; then
  echo "(reporte sanitizado -- para nicos.db + log crudo, correr con --include-database)"
fi
echo "Contenido:"
ls -la "$OUT_DIR"
