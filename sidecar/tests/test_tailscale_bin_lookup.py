"""
v0.2.9 -- hallazgo real: la app empaquetada (lanzada por LaunchServices, no
por una shell) recibe un PATH mínimo que no incluye /usr/local/bin ni
/opt/homebrew/bin, por lo que `subprocess.run(["tailscale", ...])` fallaba
con FileNotFoundError y el servidor de red se apagaba solo aunque Tailscale
estuviera corriendo. Este test reproduce ese PATH restringido.

Uso: python3 sidecar/tests/test_tailscale_bin_lookup.py
"""
import os
import sys
import unittest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

import server  # noqa: E402


class TestTailscaleBinLookup(unittest.TestCase):
    def test_encuentra_tailscale_con_path_restringido_de_gui(self):
        # simula exactamente el PATH que le da LaunchServices a una app
        # empaquetada -- sin /usr/local/bin ni /opt/homebrew/bin
        original = os.environ.get("PATH", "")
        os.environ["PATH"] = "/usr/bin:/bin:/usr/sbin:/sbin"
        try:
            binario = server._tailscale_bin()
        finally:
            os.environ["PATH"] = original
        # si tailscale no está instalado en esta máquina de test, el fallback
        # ("tailscale" a secas) es aceptable -- lo que no puede pasar es que
        # ignore los candidatos fijos si el binario SÍ está en uno de ellos
        if os.path.exists("/usr/local/bin/tailscale"):
            self.assertEqual(binario, "/usr/local/bin/tailscale")
        elif os.path.exists("/opt/homebrew/bin/tailscale"):
            self.assertEqual(binario, "/opt/homebrew/bin/tailscale")

    def test_no_rompe_si_no_esta_instalado_en_ningun_lado(self):
        import shutil
        original = shutil.which
        shutil.which = lambda *a, **k: None  # simula "no instalado" sin depender de esta máquina
        try:
            binario = server._tailscale_bin()
        finally:
            shutil.which = original
        self.assertEqual(binario, "tailscale")  # fallback sin romper


if __name__ == "__main__":
    unittest.main()
