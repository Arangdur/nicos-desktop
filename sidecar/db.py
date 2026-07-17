"""
Conexión y migraciones de NicOS Core. SQLite puro (stdlib `sqlite3`), sin ORM —
consistente con el resto del ecosistema (todo Python stdlib, sin dependencias
pesadas). La base vive junto al sidecar; es metadata de orquestación de tareas,
NUNCA la fuente de verdad financiera (esa sigue siendo foto_financiera_*.md /
el Google Sheet de Abate — ver centro_mando_adapter.py).
"""
import glob
import os
import sqlite3
import threading

DB_PATH = os.getenv(
    "NICOS_DB_PATH",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "nicos.db"),
)
MIGRATIONS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "migrations")

_local = threading.local()


def get_connection():
    """Una conexión por thread (el server es ThreadingHTTPServer)."""
    if not hasattr(_local, "conn"):
        _local.conn = sqlite3.connect(DB_PATH)
        _local.conn.row_factory = sqlite3.Row
        _local.conn.execute("PRAGMA foreign_keys = ON")
    return _local.conn


def run_migrations():
    conn = get_connection()
    conn.execute(
        "CREATE TABLE IF NOT EXISTS _migrations (filename TEXT PRIMARY KEY, applied_at TEXT NOT NULL)"
    )
    applied = {row["filename"] for row in conn.execute("SELECT filename FROM _migrations")}

    for path in sorted(glob.glob(os.path.join(MIGRATIONS_DIR, "*.sql"))):
        filename = os.path.basename(path)
        if filename in applied:
            continue
        with open(path, "r", encoding="utf-8") as f:
            sql = f.read()
        conn.executescript(sql)
        conn.execute(
            "INSERT INTO _migrations (filename, applied_at) VALUES (?, datetime('now'))",
            (filename,),
        )
        conn.commit()
        print(f"[db] migración aplicada: {filename}")
