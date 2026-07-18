#!/usr/bin/env bash
# Junta en una carpeta con fecha lo necesario para diagnosticar un problema
# durante la prueba física, sin exponer secretos:
#   - estado de Tailscale
#   - copia de nicos.db (los tokens ahí están HASHEADOS, no en texto plano --
#     ver sidecar/pairing.py _hash_token -- así que copiarla no expone
#     ningún secreto utilizable)
#   - lista de dispositivos pareados (vía sqlite3, solo texto)
#   - el log de npm start, si se corrió con "| tee <archivo>" como sugiere
#     docs/GUIA_PRUEBA_FISICA_v0.2.1.md (opcional, se busca en el Desktop)
#
# Uso:
#   cd "NicOS Desktop"
#   bash scripts/exportar_logs_mac.sh
#
# No toca ningún archivo real de Centro de Mando ni de CFO y Decisiones
# Estrategicas -- solo lee, nunca escribe fuera de la carpeta que genera.
set -euo pipefail

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

# --- nicos.db (copia -- tokens hasheados, sin secretos en texto plano) ---
DB_PATH="$SCRIPT_DIR/sidecar/nicos.db"
if [ -f "$DB_PATH" ]; then
  cp "$DB_PATH" "$OUT_DIR/nicos.db"
  if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_PATH" "SELECT device_id, device_name, user_id, paired_at, revoked_at FROM devices ORDER BY paired_at DESC;" \
      > "$OUT_DIR/dispositivos.txt" 2>&1 || echo "(no se pudo leer devices)" > "$OUT_DIR/dispositivos.txt"
    sqlite3 "$DB_PATH" "SELECT task_id, state, domain, created_at, updated_at FROM tasks ORDER BY created_at DESC LIMIT 30;" \
      > "$OUT_DIR/ultimas_tareas.txt" 2>&1 || echo "(no se pudo leer tasks)" > "$OUT_DIR/ultimas_tareas.txt"
  fi
else
  echo "(no se encontró $DB_PATH)" > "$OUT_DIR/nicos_db_no_encontrada.txt"
fi

# --- Log de npm start, si se guardó en el Desktop como sugiere la guía ---
LATEST_LOG=$(ls -t "$HOME"/Desktop/nicos-mac-*.log 2>/dev/null | head -1 || true)
if [ -n "${LATEST_LOG:-}" ]; then
  cp "$LATEST_LOG" "$OUT_DIR/npm_start.log"
fi

echo "Listo. Carpeta: $OUT_DIR"
echo "Contenido:"
ls -la "$OUT_DIR"
