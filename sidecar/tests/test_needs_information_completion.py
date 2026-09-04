"""
v0.2.1-rc6, test 7/8 pedido: completar una tarea después de aportar la
información faltante (worker.provide_missing_info) -- CERO llamados nuevos a
un proveedor de IA, reevaluación 100% determinística con lo que ya se había
extraído más lo que aporta Nicolás. Cubre también el caso "todavía no
alcanza" (se queda en needs_information con un evento nuevo, nunca en
silencio).

Uso: python3 sidecar/tests/test_needs_information_completion.py
"""
import os
import sys
import tempfile
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
import _fake_ai_clients as fake  # noqa: E402


def _fresh_modules(tmp_db_path):
    os.environ["NICOS_DB_PATH"] = tmp_db_path
    for mod in ("server", "db", "tasks", "worker", "centro_mando_adapter", "pairing", "ai_router"):
        sys.modules.pop(mod, None)
    import db as db_mod
    db_mod.run_migrations()
    import tasks as tasks_mod
    import worker as worker_mod
    import ai_router as ai_router_mod
    return db_mod, tasks_mod, worker_mod, ai_router_mod


class TestNeedsInformationCompletion(unittest.TestCase):
    def setUp(self):
        self.tmp_db = tempfile.NamedTemporaryFile(suffix=".db", delete=False)
        self.tmp_db.close()
        self.db, self.tasks, self.worker, self.ai_router = _fresh_modules(self.tmp_db.name)

    def tearDown(self):
        os.unlink(self.tmp_db.name)

    def _create_ambiguous_task(self, key="needs-info-1"):
        claude_client, _ = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown", intent="register_expense",
                                                     amount=8000, date="18/07/2026", concept="Insumos")],
        )
        result = self.tasks.create_task(key, "marianela", None, "Gasté $8.000 en insumos, no sé de qué área")
        task = result["task"]
        self.worker._process_classification(task)
        return task["task_id"], claude_client

    def test_completar_dominio_reclasifica_sin_llamar_a_ningun_proveedor(self):
        task_id, claude_client = self._create_ambiguous_task()
        needs_info = self.tasks.get_task_dict(task_id)
        self.assertEqual(needs_info["state"], "needs_information")
        calls_before = claude_client.call_count

        final = self.worker.provide_missing_info(task_id, "nicolas", {"domain": "abate"})

        self.assertEqual(final["state"], "ready")  # register_expense con todos los datos -> simple
        self.assertEqual(final["domain"], "abate")
        self.assertEqual(claude_client.call_count, calls_before, "provide_missing_info no debería llamar a ningún proveedor")

    def test_completar_con_campos_todavia_insuficientes_se_queda_en_needs_information(self):
        """Un dominio VÁLIDO (a diferencia del test de abajo) pero con la
        tarea todavía incompleta (falta amount) -- tiene que quedarse en
        needs_information con un evento nuevo, nunca en silencio."""
        claude_client, _ = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown", intent="register_expense",
                                                     amount=None, date="18/07/2026", concept="Insumos")],
        )
        result = self.tasks.create_task("needs-info-2", "marianela", None, "Gasté algo en insumos, no sé cuánto ni de qué área")
        task = result["task"]
        self.worker._process_classification(task)
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "needs_information")

        result = self.worker.provide_missing_info(task["task_id"], "nicolas", {"domain": "abate"})

        self.assertEqual(result["state"], "needs_information")
        self.assertIn("amount", result["error_message"])
        events = self.tasks.get_task_events(task["task_id"])
        # tiene que quedar un evento NUEVO -- no en silencio, aunque el estado no cambió.
        needs_info_events = [e for e in events if e["to_state"] == "needs_information"]
        self.assertGreaterEqual(len(needs_info_events), 2, "tiene que haber un evento nuevo por el intento de aclaración")

    def test_dominio_no_permitido_se_rechaza_sin_tocar_la_tarea(self):
        """v0.2.1-rc7: 'acepta únicamente dominios permitidos' -- cualquier
        valor fuera de SUPPORTED_DOMAINS (cfo/abate) se rechaza de entrada,
        sin escribir nada en la tarea ni en el historial."""
        task_id, _ = self._create_ambiguous_task(key="needs-info-domain-1")
        before = self.tasks.get_task_dict(task_id)
        events_before = len(self.tasks.get_task_events(task_id))

        for domain_invalido in ("unknown", "trading", "", "CFO", "abate ", None):
            with self.assertRaises(ValueError, msg=f"debería rechazar domain={domain_invalido!r}"):
                self.worker.provide_missing_info(task_id, "nicolas", {"domain": domain_invalido})

        after = self.tasks.get_task_dict(task_id)
        self.assertEqual(after["state"], before["state"], "el estado no debería haber cambiado")
        self.assertEqual(len(self.tasks.get_task_events(task_id)), events_before, "no debería haberse agregado ningún evento")

    def test_solo_cfo_y_abate_son_aceptados(self):
        task_id_1, _ = self._create_ambiguous_task(key="needs-info-domain-2")
        result_cfo = self.worker.provide_missing_info(task_id_1, "nicolas", {"domain": "cfo"})
        self.assertNotEqual(result_cfo["state"], "needs_information")  # domain aceptado, avanza

        task_id_2, _ = self._create_ambiguous_task(key="needs-info-domain-3")
        result_abate = self.worker.provide_missing_info(task_id_2, "nicolas", {"domain": "abate"})
        self.assertNotEqual(result_abate["state"], "needs_information")  # domain aceptado, avanza

    def test_provide_missing_info_incrementa_task_revision(self):
        task_id, _ = self._create_ambiguous_task(key="needs-info-revision-1")
        antes = self.tasks.get_task_dict(task_id)
        revision_antes = antes.get("task_revision") or 0

        despues = self.worker.provide_missing_info(task_id, "nicolas", {"domain": "abate"})

        self.assertEqual(despues["task_revision"], revision_antes + 1)

    def test_provide_missing_info_invalida_aprobaciones_anteriores(self):
        """Una tarea approval_required llega a pending_approval con un hash/
        revisión determinados; el Director pide más info (vuelve a
        needs_information); Nicolás completa el dato -- la reclasificación
        sube la revisión y cambia el hash. Un intento de aprobar con el
        hash/revisión VIEJOS tiene que fallar con StaleApproval -- es
        exactamente el mecanismo ya existente (approve_task), disparado acá
        por provide_missing_info."""
        claude_client, _ = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown", intent="new_financial_action",
                                                     amount=50000, date="18/07/2026", concept="proveedor nuevo")],
        )
        result = self.tasks.create_task("needs-info-stale-1", "marianela", None,
                                         "Autorizar un pago de $50.000 a un proveedor, no sé si es de Abate")
        task = result["task"]
        self.worker._process_classification(task)
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "needs_information")

        # domain="abate" hace que prepare_action funcione (new_financial_action
        # SÍ está soportado para abate, a diferencia de cfo -- ver
        # centro_mando_adapter.prepare_action) y termine en pending_approval.
        after_provide = self.worker.provide_missing_info(task["task_id"], "nicolas", {"domain": "abate"})
        self.assertEqual(after_provide["state"], "pending_approval")

        old_hash = after_provide["action_version_hash"]
        old_revision = after_provide["task_revision"]

        # Se pide más información de nuevo (Director no está conforme) --
        # vuelve a needs_information sin cambiar hash/revisión todavía.
        self.tasks.request_info(task["task_id"], "nicolas", "¿Quién es el proveedor exactamente?")
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "needs_information")

        # Se completa de nuevo (mismo dominio, simulando que Nicolás confirmó
        # el dato) -- esto reclasifica y sube la revisión otra vez.
        final = self.worker.provide_missing_info(task["task_id"], "nicolas", {"domain": "abate"})
        self.assertEqual(final["state"], "pending_approval")
        self.assertGreater(final["task_revision"], old_revision)
        # el hash puede coincidir si el contenido de la acción no cambió (acá
        # se volvió a mandar el mismo dominio) -- lo que SIEMPRE invalida la
        # aprobación vieja es la revisión, verificado abajo.

        # Intentar aprobar con el hash/revisión VIEJOS -- tiene que fallar.
        with self.assertRaises(self.tasks.StaleApproval):
            self.tasks.approve_task(task["task_id"], "nicolas", old_hash, approved_task_revision=old_revision)

        # Con el hash/revisión ACTUALES sí funciona.
        approved = self.tasks.approve_task(
            task["task_id"], "nicolas", final["action_version_hash"],
            approved_task_revision=final["task_revision"],
        )
        self.assertEqual(approved["state"], "ready")

    def test_provide_missing_info_registra_actor_y_evento_de_auditoria(self):
        task_id, _ = self._create_ambiguous_task(key="needs-info-audit-1")
        self.worker.provide_missing_info(task_id, "nicolas", {"domain": "abate"})

        events = self.tasks.get_task_events(task_id)
        # el evento MÁS RECIENTE originado por provide_missing_info (la
        # transición a 'classified') tiene que tener a "nicolas" como actor,
        # no "ai" ni "system" -- distingue una reclasificación humana de una
        # extracción automática.
        classified_events = [e for e in events if e["to_state"] == "classified"]
        self.assertTrue(classified_events, "debería haber un evento de clasificación")
        self.assertEqual(classified_events[-1]["actor"], "nicolas")
        # y el detail_json de ese evento tiene que reflejar de dónde salió
        # (extraction_attempts persistido, no un llamado nuevo).
        detail = classified_events[-1]["detail_json"]
        if isinstance(detail, str):
            import json as _json
            detail = _json.loads(detail)
        self.assertNotIn("extraction_provider", detail, "no debería figurar un proveedor -- no se llamó a ninguno")

    def test_completar_con_combinacion_no_preparable_termina_en_needs_review_no_en_classified(self):
        """Hallazgo del smoke test post-fix (18/7/2026): completar el dominio
        de una tarea con intent="other" (ej. un mensaje realmente fuera de
        alcance, tipo trading bot) hace que prepare_action() rechace la
        combinación (UnsupportedDomain) -- sin este resguardo, la tarea
        quedaba parada en 'classified' sin ninguna transición final."""
        claude_client, _ = fake.install_fake_clients(
            self.ai_router,
            claude_behavior=[fake.valid_extraction(domain="unknown", intent="other",
                                                     amount=None, date=None, concept=None,
                                                     evidence="mención de trading, fuera de alcance")],
        )
        result = self.tasks.create_task("needs-info-4", "marianela", None, "Cambié la posición del bot en BTC")
        task = result["task"]
        self.worker._process_classification(task)
        self.assertEqual(self.tasks.get_task_dict(task["task_id"])["state"], "needs_information")

        final = self.worker.provide_missing_info(task["task_id"], "nicolas", {"domain": "cfo"})

        self.assertEqual(final["state"], "needs_review")
        self.assertNotEqual(final["state"], "classified", "no debería quedar parada a mitad de camino")
        self.assertIn("preparar la acción", final["error_message"])

    def test_provide_missing_info_solo_aplica_a_needs_information(self):
        result = self.tasks.create_task("needs-info-3", "marianela", None, "algo")
        task_id = result["task"]["task_id"]  # sigue en 'received'
        with self.assertRaises(self.tasks.InvalidTransition):
            self.worker.provide_missing_info(task_id, "nicolas", {"domain": "cfo"})

    def test_completar_tarea_inexistente_da_valueerror(self):
        with self.assertRaises(ValueError):
            self.worker.provide_missing_info("no-existe", "nicolas", {"domain": "cfo"})


if __name__ == "__main__":
    unittest.main()
