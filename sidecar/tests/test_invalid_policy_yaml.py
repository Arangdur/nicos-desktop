"""
Punto 7 de la revisión post-v0.2.1 (parte 2): un policies/risk_policy.yaml mal
armado tiene que hacer fallar la importación de centro_mando_adapter con un
mensaje CLARO -- no un KeyError críptico la primera vez que alguien clasifica
una tarea. Prueba varios YAML inválidos por separado (subproceso nuevo cada
vez, porque la validación corre una sola vez al importar el módulo).

Uso: python3 sidecar/tests/test_invalid_policy_yaml.py
"""
import os
import subprocess
import sys
import tempfile
import unittest

SIDECAR_DIR = os.path.join(os.path.dirname(__file__), "..")


def _try_import_with_policy(yaml_content):
    """Corre la importación en un subproceso nuevo (aislado) contra un YAML
    temporal, y devuelve (returncode, stderr)."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False, encoding="utf-8") as f:
        f.write(yaml_content)
        policy_path = f.name
    try:
        env = dict(os.environ, NICOS_RISK_POLICY_PATH=policy_path)
        proc = subprocess.run(
            [sys.executable, "-c", "import centro_mando_adapter"],
            cwd=SIDECAR_DIR, env=env, capture_output=True, text=True, timeout=10,
        )
        return proc.returncode, proc.stderr
    finally:
        os.unlink(policy_path)


class TestInvalidPolicyYaml(unittest.TestCase):
    def test_yaml_vacio_falla_claro(self):
        code, stderr = _try_import_with_policy("")
        self.assertNotEqual(code, 0)
        self.assertIn("ValueError", stderr)
        self.assertNotIn("KeyError", stderr, "debe fallar con un ValueError descriptivo, no un KeyError críptico")

    def test_yaml_sin_logging_intents_falla_claro(self):
        code, stderr = _try_import_with_policy(
            "version: 1\nsupported_domains: [cfo]\nrequired_fields_cfo: [amount]\n"
        )
        self.assertNotEqual(code, 0)
        self.assertIn("ValueError", stderr)
        self.assertIn("logging_intents", stderr)

    def test_yaml_sin_version_falla_claro(self):
        code, stderr = _try_import_with_policy(
            "supported_domains: [cfo]\nlogging_intents: [register_expense]\nrequired_fields_cfo: [amount]\n"
        )
        self.assertNotEqual(code, 0)
        self.assertIn("version", stderr)

    def test_supported_domains_vacio_falla_claro(self):
        code, stderr = _try_import_with_policy(
            "version: 1\nsupported_domains: []\nlogging_intents: [register_expense]\nrequired_fields_cfo: [amount]\n"
        )
        self.assertNotEqual(code, 0)
        self.assertIn("supported_domains", stderr)

    def test_yaml_no_es_un_mapeo_falla_claro(self):
        # Una lista suelta en vez de un diccionario.
        code, stderr = _try_import_with_policy("- item1\n- item2\n")
        self.assertNotEqual(code, 0)
        self.assertIn("ValueError", stderr)

    def test_yaml_valido_no_falla(self):
        code, stderr = _try_import_with_policy(
            "version: 1\nsupported_domains: [cfo, abate]\nlogging_intents: [register_expense, register_income]\n"
            "required_fields_cfo: [amount, date, concept]\n"
        )
        self.assertEqual(code, 0, f"un YAML válido no debería fallar la importación. stderr: {stderr}")


if __name__ == "__main__":
    unittest.main()
