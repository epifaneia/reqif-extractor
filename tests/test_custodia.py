"""Pruebas locales de los tres cables de custodia. Sin dependencias, sin red.

    python tests/test_custodia.py

Lo que se prueba aquí es lo que existe en el repo: actor por variable de
entorno, libro de eventos y manifiesto de firma con clave local. Lo que NO se
prueba, porque no existe hasta que una empresa lo conecta, es el adaptador a su
proveedor de identidad, su SIEM y su mecanismo de firma.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from custodia import identity, ledger, signoff  # noqa: E402

fallos = 0


def check(cond: bool, msg: str) -> None:
    global fallos
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        fallos += 1


def main() -> int:
    tmp = Path(tempfile.mkdtemp())

    # --- identidad ---
    os.environ.pop(identity.ENV_VAR, None)
    try:
        identity.current_actor()
        check(False, "sin actor debe fallar")
    except identity.MissingActor:
        check(True, "sin actor → MissingActor")
    os.environ[identity.ENV_VAR] = "ana.garcia"
    check(identity.current_actor() == "ana.garcia", "actor por variable de entorno")
    identity.set_provider(lambda: "sso:ana@empresa.com")
    check(identity.current_actor() == "sso:ana@empresa.com", "proveedor sustituible")
    identity.set_provider(identity._env_provider)

    # --- ledger ---
    led = ledger.Ledger(tmp / "custody.jsonl")
    with led.call(step="s4", engine="ollama", endpoint="127.0.0.1:11434",
                  leaves_machine=False, payload="hola mundo") as rec:
        rec["output_chars"] = 3
    try:
        with led.call(step="s4", engine="gemini", endpoint="generativelanguage.googleapis.com",
                      leaves_machine=True, payload="x" * 500):
            raise ValueError("simulado")
    except ValueError:
        pass
    lines = [json.loads(l) for l in (tmp / "custody.jsonl").read_text(encoding="utf-8").splitlines()]
    check(len(lines) == 2, "dos eventos escritos")
    check(lines[0]["actor"] == "ana.garcia" and lines[0]["ok"] is True, "evento con actor y ok")
    check(lines[1]["ok"] is False and lines[1]["error"] == "ValueError", "el error se registra y se relanza")
    check("hola" not in (tmp / "custody.jsonl").read_text(encoding="utf-8"), "el texto de entrada NO está en el ledger")
    s = led.summary()
    check(s["gemini"]["left_machine"] == 1 and s["ollama"]["left_machine"] == 0, "resumen: qué salió y qué no")
    check(ledger.is_local("http://127.0.0.1:11434/api") and not ledger.is_local("https://generativelanguage.googleapis.com"), "is_local")

    # --- firma ---
    out = tmp / "salida.reqif"
    out.write_text("<REQ-IF/>", encoding="utf-8")
    os.environ.pop(signoff.KEY_VAR, None)
    try:
        signoff.sign(out, "ana.garcia")
        check(False, "firmar sin clave debe fallar")
    except signoff.NotSigned:
        check(True, "firmar sin clave → NotSigned")
    ok, why = signoff.verify(out)
    check(not ok and "sin manifiesto" in why, "sin manifiesto → no verifica")
    os.environ[signoff.KEY_VAR] = "clave-local-de-prueba"
    mp = signoff.sign(out, "ana.garcia")
    check(mp.is_file(), "manifiesto creado")
    ok, why = signoff.verify(out)
    check(ok and "ana.garcia" in why, "firma válida verifica")
    m = signoff.require_signoff(out)
    check(m["actor"] == "ana.garcia", "require_signoff devuelve el manifiesto")
    out.write_text("<REQ-IF>cambiado</REQ-IF>", encoding="utf-8")
    ok, why = signoff.verify(out)
    check(not ok and "cambió" in why, "fichero modificado tras la firma → no verifica")
    out.write_text("<REQ-IF/>", encoding="utf-8")
    os.environ[signoff.KEY_VAR] = "otra-clave"
    ok, why = signoff.verify(out)
    check(not ok and "no verifica" in why, "clave distinta → la firma no verifica")

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} fallos'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
