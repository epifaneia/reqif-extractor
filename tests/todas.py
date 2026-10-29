"""Corre todas las pruebas locales, cada una en su proceso. Sin red, sin API.

    python tests/todas.py

Sale con código distinto de 0 si alguna falla.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent


def main() -> int:
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    malas = []
    for t in sorted(HERE.glob("test_*.py")):
        print(f"== {t.name}", flush=True)
        r = subprocess.run([sys.executable, str(t)], env=env)
        if r.returncode:
            malas.append(t.name)
    print(f"\n{'TODAS OK' if not malas else 'FALLAN: ' + ', '.join(malas)}")
    return 1 if malas else 0


if __name__ == "__main__":
    raise SystemExit(main())
