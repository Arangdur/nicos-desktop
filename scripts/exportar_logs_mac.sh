#!/usr/bin/env bash
# Junta en una carpeta con fecha lo necesario para diagnosticar un problema
# durante la prueba física.
#
# Por defecto genera un REPORTE SANITIZADO, no una copia de nicos.db: la base
# puede tener texto original de tareas, importes, resultados financieros,
# mensajes de error con detalle operativo, o datos ingresados sin querer --
# nada de eso sale por defecto. El reporte sanitizado tiene conteos por
# estado, IDs, timestamps y metadata de esquema, sin el contenido de
# raw_text/extracted_json/result_json/error_message/detail_json.
#
# La copia ÍNTEGRA de nicos.db (para depurar algo puntual que el reporte
# sanitizado no alcanza a explicar) requiere el flag explícito
# --include-database, y el script avisa antes de hacerlo.
#
# Uso:
#   cd "NicOS Desktop"
#   bash scripts/exportar_logs_mac.sh                    # solo reporte sanitizado
#   bash scripts/exportar_logs_mac.sh --include-database  # + copia íntegra de nicos.db
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

# --- Tailscale ---
{
  echo "=== tailscale status ==="
  tailscale status 2>&1 || echo "(tailscale no disponible o no logueado)"
  echo
  echo "=== tailscale ip -4 ==="
  tailscale ip -4 2>&1 || echo "(sin IP asignada)"
} > "$OUT_DIR/tailscale.txt"

# --- Puerto de red del sidecar ---
{
  echo "=== lsof -iTCP:47500 ==="
  lsof -iTCP:47500 -sTCP:LISTEN 2>&1 || echo "(nada escuchando en 47500)"
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
  echo "(no se encontró $DB_PATH o falta sqlite3)" > "$OUT_DIR/nicos_db_no_disponible.txt"
fi

# --- Log de npm start, si se guardó en el Desktop como sugiere la guía ---
LATEST_LOG=$(ls -t "$HOME"/Desktop/nicos-mac-*.log 2>/dev/null | head -1 || true)
if [ -n "${LATEST_LOG:-}" ]; then
  cp "$LATEST_LOG" "$OUT_DIR/npm_start.log"
fi

echo "Listo. Carpeta: $OUT_DIR"
if [ "$INCLUDE_DB" = false ]; then
  echo "(reporte sanitizado -- para la copia íntegra de nicos.db, correr con --include-database)"
fi
echo "Contenido:"
ls -la "$OUT_DIR"
