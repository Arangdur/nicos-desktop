#!/usr/bin/env python3
"""
Corre todos los test_*.py de esta carpeta, en orden, y resume pass/fail.
Cada archivo es un proceso Python nuevo (no se importan como módulos de
unittest en el mismo proceso) porque varios de ellos re-setean variables de
entorno y reimportan módulos del sidecar con importlib -- mezclarlos en un
mismo proceso arriesgaría contaminación de estado entre archivos.

Uso: python3 sidecar/tests/run_all.py
"""
import glob
import os
import subprocess
import sys

TESTS_DIR = os.path.dirname(os.path.abspath(__file__))


def main():
    test_files = sorted(
        f for f in glob.glob(os.path.join(TESTS_DIR, "test_*.py"))
    )
    results = []
    for path in test_files:
        name = os.path.basename(path)
        proc = subprocess.run(
            [sys.executable, path], cwd=TESTS_DIR, capture_output=True, text=True, timeout=120,
        )
        ok = proc.returncode == 0
        results.append((name, ok, proc.stdout, proc.stderr))
        status = "OK" if ok else "FALLÓ"
        print(f"[{status}] {name}")

    print()
    n_ok = sum(1 for _, ok, _, _ in results if ok)
    print(f"{n_ok}/{len(results)} archivos de test pasaron.")

    fallidos = [r for r in results if not r[1]]
    if fallidos:
        print("\n--- Detalle de fallos ---")
        for name, _, stdout, stderr in fallidos:
            print(f"\n=== {name} ===")
            print(stderr[-3000:])
        sys.exit(1)


if __name__ == "__main__":
    main()
