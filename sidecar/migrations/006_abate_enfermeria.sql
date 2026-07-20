-- v0.2.3 -- módulo real de Enfermería de Abate: residentes, tratamiento
-- farmacológico, administración de medicación y novedades. Sin IA en
-- ningún punto (confirmado con Nicolás, ver NICOS_MASTER_SCOPE.md §3) --
-- las novedades se guardan tal cual las escribe el enfermero, nunca pasan
-- por ai_router.py.

CREATE TABLE IF NOT EXISTS abate_residentes (
  residente_id TEXT PRIMARY KEY,
  nombre TEXT NOT NULL,
  activo INTEGER NOT NULL DEFAULT 1,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(user_id)
);

-- Un solo tratamiento vigente por residente -- el Director lo reemplaza
-- entero cada vez que cambia algo (no hay historial de versiones todavía,
-- a propósito: la prioridad es que el enfermero siempre vea el vigente sin
-- ambigüedad, no reconstruir cambios pasados).
CREATE TABLE IF NOT EXISTS abate_tratamientos (
  residente_id TEXT PRIMARY KEY REFERENCES abate_residentes(residente_id),
  medicacion_json TEXT NOT NULL,  -- [{droga, dosis, horarios: ["08:00", ...]}, ...]
  indicaciones TEXT NOT NULL DEFAULT '',
  updated_at TEXT NOT NULL,
  updated_by TEXT NOT NULL REFERENCES users(user_id)
);

-- Una fila por toma efectivamente administrada -- nunca se borra ni se
-- edita, es auditoría real de qué enfermero dio qué medicación y cuándo.
CREATE TABLE IF NOT EXISTS abate_administraciones (
  administracion_id TEXT PRIMARY KEY,
  residente_id TEXT NOT NULL REFERENCES abate_residentes(residente_id),
  droga TEXT NOT NULL,
  horario_previsto TEXT NOT NULL,  -- "08:00", tal cual figura en el tratamiento vigente al momento de administrar
  fecha TEXT NOT NULL,             -- "YYYY-MM-DD", día calendario de la toma
  administrado_at TEXT NOT NULL,   -- timestamp real de cuándo se tildó
  administrado_by TEXT NOT NULL REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_abate_administraciones_residente_fecha
  ON abate_administraciones(residente_id, fecha);

-- Texto tal cual lo escribe el enfermero -- sin procesar por ninguna IA.
CREATE TABLE IF NOT EXISTS abate_novedades (
  novedad_id TEXT PRIMARY KEY,
  residente_id TEXT NOT NULL REFERENCES abate_residentes(residente_id),
  categoria TEXT NOT NULL CHECK(categoria IN ('operativo', 'clinico_conductual', 'administrativo')),
  texto TEXT NOT NULL,
  created_at TEXT NOT NULL,
  created_by TEXT NOT NULL REFERENCES users(user_id)
);
CREATE INDEX IF NOT EXISTS idx_abate_novedades_residente
  ON abate_novedades(residente_id, created_at);
