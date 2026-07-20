"""
Módulo real de Enfermería de Abate -- residentes, tratamiento farmacológico,
administración de medicación y novedades. Sin IA en ningún punto: las
novedades se guardan tal cual las escribe el enfermero (`create_novedad`
nunca llama a `ai_router`), confirmado con Nicolás (NICOS_MASTER_SCOPE.md §3).

Permisos (aplicados acá Y en server.py, defensa en profundidad):
- Alta de residentes y fijar el tratamiento vigente: Director-only (server.py
  ya bloquea esto con el mismo guard is_lan()->403 que usan las demás rutas
  admin -- este módulo no necesita repetirlo).
- Registrar administración de medicación y cargar novedades: Enfermero-only
  (server.py chequea auth["role"] == "enfermero" antes de llamar acá).
- Operativa: sin acceso a nada de este módulo (ver tabla de permisos §6).
"""
import datetime
import secrets

import db


def _now_iso():
    return datetime.datetime.utcnow().isoformat()


def _today():
    return datetime.date.today().isoformat()


class AbateError(Exception):
    pass


# ---- Residentes -------------------------------------------------------

def list_residentes(solo_activos: bool = True):
    conn = db.get_connection()
    query = "SELECT residente_id, nombre, activo, created_at FROM abate_residentes"
    if solo_activos:
        query += " WHERE activo = 1"
    query += " ORDER BY nombre"
    return [dict(r) for r in conn.execute(query).fetchall()]


def create_residente(nombre: str, created_by: str):
    if not nombre or not nombre.strip():
        raise AbateError("Falta el nombre del residente.")
    conn = db.get_connection()
    residente_id = secrets.token_hex(6)
    conn.execute(
        "INSERT INTO abate_residentes (residente_id, nombre, activo, created_at, created_by) "
        "VALUES (?, ?, 1, ?, ?)",
        (residente_id, nombre.strip(), _now_iso(), created_by),
    )
    conn.commit()
    return {"residente_id": residente_id, "nombre": nombre.strip()}


def set_activo(residente_id: str, activo: bool):
    conn = db.get_connection()
    conn.execute(
        "UPDATE abate_residentes SET activo = ? WHERE residente_id = ?",
        (1 if activo else 0, residente_id),
    )
    conn.commit()


def _residente_existe(residente_id: str) -> bool:
    conn = db.get_connection()
    return conn.execute(
        "SELECT 1 FROM abate_residentes WHERE residente_id = ?", (residente_id,)
    ).fetchone() is not None


# ---- Tratamiento farmacológico (Director-only para escribir) ----------

def get_tratamiento(residente_id: str):
    conn = db.get_connection()
    row = conn.execute(
        "SELECT residente_id, medicacion_json, indicaciones, updated_at, updated_by "
        "FROM abate_tratamientos WHERE residente_id = ?",
        (residente_id,),
    ).fetchone()
    if row is None:
        return None
    import json
    return {
        "residente_id": row["residente_id"],
        "medicacion": json.loads(row["medicacion_json"]),
        "indicaciones": row["indicaciones"],
        "updated_at": row["updated_at"],
        "updated_by": row["updated_by"],
    }


def set_tratamiento(residente_id: str, medicacion: list, indicaciones: str, updated_by: str):
    if not _residente_existe(residente_id):
        raise AbateError("Ese residente no existe.")
    if not isinstance(medicacion, list):
        raise AbateError("La medicación tiene que ser una lista.")
    for item in medicacion:
        if not item.get("droga") or not item.get("dosis") or not item.get("horarios"):
            raise AbateError("Cada droga necesita nombre, dosis y al menos un horario.")

    import json
    conn = db.get_connection()
    now = _now_iso()
    conn.execute(
        "INSERT INTO abate_tratamientos (residente_id, medicacion_json, indicaciones, updated_at, updated_by) "
        "VALUES (?, ?, ?, ?, ?) "
        "ON CONFLICT(residente_id) DO UPDATE SET "
        "medicacion_json = excluded.medicacion_json, indicaciones = excluded.indicaciones, "
        "updated_at = excluded.updated_at, updated_by = excluded.updated_by",
        (residente_id, json.dumps(medicacion), indicaciones or "", now, updated_by),
    )
    conn.commit()


# ---- Administración de medicación (Enfermero-only) ---------------------

def list_administraciones_hoy(residente_id: str):
    conn = db.get_connection()
    rows = conn.execute(
        "SELECT a.administracion_id, a.droga, a.horario_previsto, a.administrado_at, "
        "a.administrado_by, u.display_name as administrado_by_nombre "
        "FROM abate_administraciones a JOIN users u ON u.user_id = a.administrado_by "
        "WHERE a.residente_id = ? AND a.fecha = ? ORDER BY a.administrado_at",
        (residente_id, _today()),
    ).fetchall()
    return [dict(r) for r in rows]


def registrar_administracion(residente_id: str, droga: str, horario_previsto: str, administrado_by: str):
    if not _residente_existe(residente_id):
        raise AbateError("Ese residente no existe.")
    tratamiento = get_tratamiento(residente_id)
    if not tratamiento:
        raise AbateError("Este residente todavía no tiene un tratamiento cargado.")
    droga_valida = any(
        item["droga"] == droga and horario_previsto in item["horarios"]
        for item in tratamiento["medicacion"]
    )
    if not droga_valida:
        raise AbateError("Esa droga/horario no figura en el tratamiento vigente -- puede que el Director lo haya actualizado. Recargá la pantalla.")

    conn = db.get_connection()
    # Idempotencia real: si esta toma exacta (residente+droga+horario+fecha) ya
    # está registrada, no se duplica -- un doble-click accidental no genera dos
    # filas de auditoría para la misma toma.
    ya_existe = conn.execute(
        "SELECT 1 FROM abate_administraciones WHERE residente_id=? AND droga=? AND horario_previsto=? AND fecha=?",
        (residente_id, droga, horario_previsto, _today()),
    ).fetchone()
    if ya_existe:
        raise AbateError("Esa toma ya estaba registrada.")

    administracion_id = secrets.token_hex(8)
    conn.execute(
        "INSERT INTO abate_administraciones "
        "(administracion_id, residente_id, droga, horario_previsto, fecha, administrado_at, administrado_by) "
        "VALUES (?, ?, ?, ?, ?, ?, ?)",
        (administracion_id, residente_id, droga, horario_previsto, _today(), _now_iso(), administrado_by),
    )
    conn.commit()
    return {"administracion_id": administracion_id}


# ---- Novedades (Enfermero-only, texto tal cual, SIN IA) ----------------

CATEGORIAS_VALIDAS = {"operativo", "clinico_conductual", "administrativo"}


def list_novedades(residente_id: str = None, categoria: str = None, limit: int = 100):
    conn = db.get_connection()
    query = (
        "SELECT n.novedad_id, n.residente_id, r.nombre as residente_nombre, n.categoria, n.texto, "
        "n.created_at, n.created_by, u.display_name as created_by_nombre "
        "FROM abate_novedades n "
        "JOIN abate_residentes r ON r.residente_id = n.residente_id "
        "JOIN users u ON u.user_id = n.created_by WHERE 1=1"
    )
    params = []
    if residente_id:
        query += " AND n.residente_id = ?"
        params.append(residente_id)
    if categoria:
        query += " AND n.categoria = ?"
        params.append(categoria)
    query += " ORDER BY n.created_at DESC LIMIT ?"
    params.append(limit)
    return [dict(r) for r in conn.execute(query, params).fetchall()]


def create_novedad(residente_id: str, categoria: str, texto: str, created_by: str):
    if not _residente_existe(residente_id):
        raise AbateError("Ese residente no existe.")
    if categoria not in CATEGORIAS_VALIDAS:
        raise AbateError(f"Categoría inválida -- tiene que ser una de: {', '.join(sorted(CATEGORIAS_VALIDAS))}.")
    if not texto or not texto.strip():
        raise AbateError("Falta el texto de la novedad.")

    conn = db.get_connection()
    novedad_id = secrets.token_hex(8)
    # texto.strip() tal cual -- ninguna llamada a ai_router acá, a propósito.
    conn.execute(
        "INSERT INTO abate_novedades (novedad_id, residente_id, categoria, texto, created_at, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?)",
        (novedad_id, residente_id, categoria, texto.strip(), _now_iso(), created_by),
    )
    conn.commit()
    return {"novedad_id": novedad_id}
