"""
Punto 9 de la revisión post-v0.2.1 (informe de pruebas): "aprobación
invalidada por cambio de revisión/hash". Esto se verificó manualmente en una
sesión anterior (4 escenarios) pero nunca quedó como test automatizado
permanente -- este archivo cierra ese gap.

approve_task() exige que TANTO el hash de acción COMO la revisión coincidan
con los valores actuales de la tarea -- si cualquiera de los dos cambió desde
que se generó la vista de aprobación (ej. la tarea se reclasificó), la
aprobación se rechaza con StaleApproval.

Uso: python3 sidecar/tests/test_stale_approval.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    return db_mod, tasks_mod


class TestStaleApproval(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _make_pending_approval_task(self, monto=10000):
        result = self.tasks.create_task(f"stale-test-{monto}", "nicolas", None, "Gasté algo")
        task_id = result["task"]["task_id"]
        prepared = {"domain": "cfo", "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": monto, "tipo": "gasto"}}
        action_hash = self.tasks.compute_action_hash(prepared)
        self.tasks.transition(task_id, "parsing", "system")
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared["args"], risk_level="approval_required", task_revision=1)
        self.tasks.transition(task_id, "pending_approval", "system", action_version_hash=action_hash,
                               detail={"prepared_action": prepared, "task_revision": 1})
        return task_id, action_hash, prepared

    def test_hash_correcto_y_revision_correcta_aprueba(self):
        task_id, action_hash, _ = self._make_pending_approval_task()
        result = self.tasks.approve_task(task_id, "nicolas", action_hash, approved_task_revision=1)
        self.assertEqual(result["state"], "ready")

    def test_hash_desactualizado_rechaza(self):
        task_id, action_hash, _ = self._make_pending_approval_task()
        with self.assertRaises(self.tasks.StaleApproval):
            self.tasks.approve_task(task_id, "nicolas", "hash-completamente-inventado", approved_task_revision=1)

    def test_revision_desactualizada_rechaza(self):
        task_id, action_hash, _ = self._make_pending_approval_task()
        with self.assertRaises(self.tasks.StaleApproval):
            self.tasks.approve_task(task_id, "nicolas", action_hash, approved_task_revision=999)

    def test_reclasificacion_cambia_hash_y_revision_invalida_aprobacion_vieja(self):
        """El escenario real: el Director ve la tarea (hash H1, revisión 1),
        pero ANTES de que apruebe, algo la reclasifica (ej. needs_information ->
        classified de nuevo con datos corregidos) -- la aprobación vieja, con
        el hash/revisión que el Director vio originalmente, ya no debe servir."""
        task_id, action_hash_viejo, _ = self._make_pending_approval_task(monto=10000)

        # Reclasificación: vuelve a needs_information y de ahí a classified con
        # un monto distinto -- nuevo hash, nueva revisión.
        self.tasks.transition(task_id, "needs_information", "system", detail={"pregunta": "¿el monto es correcto?"})
        prepared_nuevo = {"domain": "cfo", "args": {"fecha": "2026-07-17", "concepto": "algo", "monto": 15000, "tipo": "gasto"}}
        action_hash_nuevo = self.tasks.compute_action_hash(prepared_nuevo)
        self.tasks.transition(task_id, "classified", "ai", domain="cfo", intent="register_expense",
                               extracted_json=prepared_nuevo["args"], risk_level="approval_required", task_revision=2)
        self.tasks.transition(task_id, "pending_approval", "system", action_version_hash=action_hash_nuevo,
                               detail={"prepared_action": prepared_nuevo, "task_revision": 2})

        self.assertNotEqual(action_hash_viejo, action_hash_nuevo, "el monto cambió, el hash tiene que cambiar")

        # La aprobación con los valores VIEJOS (lo que el Director vio antes de
        # la reclasificación) tiene que rechazarse.
        with self.assertRaises(self.tasks.StaleApproval):
            self.tasks.approve_task(task_id, "nicolas", action_hash_viejo, approved_task_revision=1)

        # La aprobación con los valores NUEVOS (recargando la tarea) sí funciona.
        result = self.tasks.approve_task(task_id, "nicolas", action_hash_nuevo, approved_task_revision=2)
        self.assertEqual(result["state"], "ready")

    def test_approved_task_revision_none_solo_valida_hash_compatibilidad(self):
        # approved_task_revision es opcional (compatibilidad con clientes
        # viejos que todavía no lo mandan) -- si se omite, solo se valida el hash.
        task_id, action_hash, _ = self._make_pending_approval_task()
        result = self.tasks.approve_task(task_id, "nicolas", action_hash, approved_task_revision=None)
        self.assertEqual(result["state"], "ready")


if __name__ == "__main__":
    unittest.main()
