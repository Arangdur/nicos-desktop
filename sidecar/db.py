"""
Conexión y migraciones de NicOS Core. SQLite puro (stdlib `sqlite3`), sin ORM —
consistente con el resto del ecosistema (todo Python stdlib, sin dependencias
pesadas). La base vive junto al sidecar; es metadata de orquestación de tareas,
NUNCA la fuente de verdad financiera (esa sigue siendo foto_financiera_*.md /
el Google Sheet de Abate — ver centro_mando_adapter.py).

Hardening (post-revisión v0.2.1): WAL + busy_timeout para tolerar el worker y
los handlers HTTP escribiendo concurrentemente, permisos 600 (nicos.db contiene
aprobaciones y auditoría, no es descartable), integrity_check al arrancar, y
backup automático antes de cada migración nueva y periódicamente en uso normal.
"""
import datetime
import glob
import os
import shutil
import sqlite3
import stat
import sys
import threading

DB_PATH = os.getenv(
    "NICOS_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "nicos.db"),
)
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")
# Al lado de DB_PATH, no de __file__ -- así un NICOS_DB_PATH de prueba (ver
# tests/test_lan_admin_routes_403.py) nunca deja backups en el sidecar/backups/
# real, y sigue siendo el mismo directorio de siempre para el caso normal
# (DB_PATH default ya vive en sidecar/).
BACKUPS_DIR = os.path.join(os.path.dirname(os.path.abspath(DB_PATH)), "backups")
BACKUP_RETENTION_DAYS = 14

_local = threading.local()


def _harden_permissions():
    if os.path.exists(DB_PATH):
        try:
            os.chmod(DB_PATH, stat.S_IRUSR | stat.S_IWUSR)  # 0o600, solo el dueño
        except OSError as e:
            sys.stderr.write(f"[db] no se pudieron restringir permisos de {DB_PATH}: {e}\n")


def get_connection():
    """Una conexión por thread (el server es ThreadingHTTPServer + el worker loop)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
        _local.conn.execute("PRAGMA journal_mode = WAL")
        _local.conn.execute("PRAGMA busy_timeout = 5000")
    return _local.conn


def backup_now(label="manual"):
    """Copia nicos.db a backups/. Se usa antes de migrar y periódicamente desde
    el worker loop (no en cada request — sería demasiado). Rotación simple:
    conserva los últimos BACKUP_RETENTION_DAYS archivos."""
    if not os.path.exists(DB_PATH):
        return None
    os.makedirs(BACKUPS_DIR, exist_ok=True)
    stamp = datetime.datetime.utcnow().strftime("%Y-%m-%d")
    dest = os.path.join(BACKUPS_DIR, f"nicos-{stamp}-{label}.db")
    if os.path.exists(dest):
        return dest  # ya hay backup de hoy con esta etiqueta, no lo pisamos
    # get_connection().commit() ya corrió antes de llamar acá en los call sites;
    # usamos la API de backup de sqlite3 en vez de copiar el archivo a mano para
    # no capturar un WAL a medio escribir.
    src_conn = sqlite3.connect(DB_PATH)
    dest_conn = sqlite3.connect(dest)
    with dest_conn:
        src_conn.backup(dest_conn)
    src_conn.close()
    dest_conn.close()
    _prune_old_backups()
    return dest


def _prune_old_backups():
    if not os.path.isdir(BACKUPS_DIR):
        return
    files = sorted(glob.glob(os.path.join(BACKUPS_DIR, "nicos-*.db")))
    cutoff = datetime.datetime.utcnow() - datetime.timedelta(days=BACKUP_RETENTION_DAYS)
    for path in files:
        mtime = datetime.datetime.utcfromtimestamp(os.path.getmtime(path))
        if mtime < cutoff:
            try:
                os.remove(path)
            except OSError:
                pass


def integrity_check():
    conn = get_connection()
    result = conn.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        sys.stderr.write(f"[db] ADVERTENCIA — integrity_check devolvió: {result}\n")
    else:
        sys.stderr.write("[db] integrity_check: ok\n")
    return result == "ok"


def run_migrations():
    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row["filename"] for row in conn.execute("SELECT filename FROM _migrations")}

    pending = [
        p for p in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql")))
        if os.path.basename(p) not in applied
    ]
    if pending and os.path.exists(DB_PATH) and os.path.getsize(DB_PATH) > 0:
        conn.commit()
        backup_path = backup_now(label="pre-migracion")
        if backup_path:
            print(f"[db] backup pre-migración: {backup_path}")

    for path in pending:
        filename = os.path.basename(path)
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (filename,),
        )
        conn.commit()
        print(f"[db] migración aplicada: {filename}")

    _harden_permissions()
