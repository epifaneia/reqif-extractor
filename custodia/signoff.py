"""signoff — la salida es una propuesta hasta que alguien la firma. Tercer cable.

Un manifiesto de firma es un JSON al lado del fichero de salida:

    <fichero>.signoff.json
    {"file": "X.reqif", "sha256": "...", "actor": "ana.garcia", "ts": "...",
     "scheme": "hmac-sha256", "signature": "..."}

`sign()` lo crea con la clave de CUSTODIA_SIGNING_KEY (HMAC-SHA256 sobre
sha256 + actor + ts). `verify()` comprueba que el fichero no cambió desde la
firma y que la firma es válida. `require_signoff()` lanza NotSigned si falta o
no verifica: es lo que un paso de exportación/import llama antes de dejar salir
nada que haya tocado el modelo.

La empresa sustituye el esquema HMAC por el suyo (PKI corporativa, flujo de
aprobación de Polarion, firma electrónica) implementando dos funciones con la
misma forma que `_hmac_sign` / `_hmac_verify` y registrándolas con
`set_scheme()`. El manifiesto y la comprobación de hash no cambian.

Estado: implementado (HMAC con clave local). El adaptador a PKI o a un flujo
de aprobación corporativo es trabajo de integración por empresa.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
import time
from pathlib import Path
from typing import Callable

KEY_VAR = "CUSTODIA_SIGNING_KEY"


class NotSigned(RuntimeError):
    """Falta el manifiesto, el fichero cambió, o la firma no verifica."""


def sha256_of(path: Path) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as f:
        for block in iter(lambda: f.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _payload(m: dict) -> bytes:
    return f'{m["sha256"]}|{m["actor"]}|{m["ts"]}'.encode("utf-8")


def _hmac_sign(m: dict) -> str:
    key = os.environ.get(KEY_VAR, "")
    if not key:
        raise NotSigned(f"Sin clave de firma: exporta {KEY_VAR} o registra un esquema con set_scheme().")
    return hmac.new(key.encode("utf-8"), _payload(m), hashlib.sha256).hexdigest()


def _hmac_verify(m: dict) -> bool:
    key = os.environ.get(KEY_VAR, "")
    if not key:
        return False
    expected = hmac.new(key.encode("utf-8"), _payload(m), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, m.get("signature", ""))


_scheme_name = "hmac-sha256"
_sign: Callable[[dict], str] = _hmac_sign
_verify: Callable[[dict], bool] = _hmac_verify


def set_scheme(name: str, sign_fn: Callable[[dict], str], verify_fn: Callable[[dict], bool]) -> None:
    """Sustituye HMAC por el mecanismo de firma de la empresa."""
    global _scheme_name, _sign, _verify
    _scheme_name, _sign, _verify = name, sign_fn, verify_fn


def manifest_path(path: Path) -> Path:
    p = Path(path)
    return p.with_name(p.name + ".signoff.json")


def sign(path: Path, actor: str) -> Path:
    """Crea <fichero>.signoff.json. El actor viene de identity.current_actor()."""
    p = Path(path)
    if not p.is_file():
        raise NotSigned(f"no existe {p}")
    m = {"file": p.name, "sha256": sha256_of(p), "actor": actor,
         "ts": time.strftime("%Y-%m-%dT%H:%M:%S%z"), "scheme": _scheme_name}
    m["signature"] = _sign(m)
    out = manifest_path(p)
    out.write_text(json.dumps(m, ensure_ascii=False, indent=2), encoding="utf-8")
    return out


def verify(path: Path) -> tuple[bool, str]:
    """→ (ok, motivo). No lanza: para listar estados."""
    p = Path(path)
    mp = manifest_path(p)
    if not mp.is_file():
        return False, "sin manifiesto de firma"
    try:
        m = json.loads(mp.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return False, "manifiesto ilegible"
    if m.get("file") != p.name:
        return False, "el manifiesto es de otro fichero"
    if m.get("sha256") != sha256_of(p):
        return False, "el fichero cambió después de la firma"
    if m.get("scheme") != _scheme_name:
        return False, f"esquema de firma {m.get('scheme')!r} no es el configurado ({_scheme_name})"
    if not m.get("actor"):
        return False, "manifiesto sin actor"
    if not _verify(m):
        return False, "la firma no verifica"
    return True, f"firmado por {m['actor']} el {m['ts']}"


def require_signoff(path: Path) -> dict:
    """Lanza NotSigned si el fichero no está firmado y verificado. Devuelve el manifiesto."""
    ok, why = verify(path)
    if not ok:
        raise NotSigned(f"{Path(path).name}: {why}")
    return json.loads(manifest_path(path).read_text(encoding="utf-8"))
