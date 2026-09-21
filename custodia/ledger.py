"""ledger — libro de custodia: qué salió de la máquina, cuándo, hacia dónde y quién. Segundo cable.

Cada llamada a un modelo se registra con lo mínimo para auditar sin exponer el
dato: actor, paso, motor, endpoint, si el dato sale de la máquina, tamaño en
caracteres y un hash de la entrada. NUNCA el texto en claro. De este fichero
sale, medio escrita, la ficha de custodia que acompaña a cada solución.

Formato: una línea JSON por evento (JSONL), append-only, en la ruta de
CUSTODIA_LEDGER (por defecto logs/custody.jsonl). La empresa no tiene que
escribir nada para integrarlo: apunta el agente de su SIEM (Splunk, Sentinel,
ELK) a ese fichero, o redirige a stdout con CUSTODIA_LEDGER=-.

Uso mínimo, en el cliente del modelo:

    from custodia.ledger import record_call
    with record_call(step="s4_evaluate", engine="ollama", endpoint=url,
                     leaves_machine=False, payload=body_json) as rec:
        raw = http_post(url, body)
        rec["output_chars"] = len(raw)

Estado: implementado. El envío al SIEM corporativo es configuración por empresa.
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from contextlib import contextmanager
from pathlib import Path

ENV_VAR = "CUSTODIA_LEDGER"
DEFAULT_PATH = "logs/custody.jsonl"

EVENT_SCHEMA = {
    "ts": "ISO-8601 local",
    "actor": "identity.current_actor(), o 'unknown' si el proveedor no está configurado",
    "step": "paso del pipeline que hace la llamada",
    "engine": "ollama | gemini | groq | ...",
    "endpoint": "host del servicio",
    "leaves_machine": "bool: el dato sale de la máquina",
    "input_chars": "tamaño de la entrada",
    "input_sha256_16": "hash truncado de la entrada; nunca el texto",
    "output_chars": "tamaño de la salida (si el llamador lo rellena)",
    "ok": "bool", "error": "nombre de la excepción si falló", "seconds": "duración",
}


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]


def _actor() -> str:
    try:
        from . import identity
        return identity.current_actor()
    except Exception:
        return "unknown"


class Ledger:
    def __init__(self, path: str | Path | None = None):
        raw = path or os.environ.get(ENV_VAR) or DEFAULT_PATH
        self.to_stdout = raw == "-"
        self.path = None if self.to_stdout else Path(raw)
        if self.path:
            self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, rec: dict) -> None:
        line = json.dumps(rec, ensure_ascii=False)
        if self.to_stdout:
            print(line, file=sys.stdout, flush=True)
        else:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(line + "\n")

    @contextmanager
    def call(self, step: str, engine: str, endpoint: str, leaves_machine: bool,
             payload: str, **extra):
        rec = {
            "ts": time.strftime("%Y-%m-%dT%H:%M:%S"),
            "actor": _actor(),
            "step": step,
            "engine": engine,
            "endpoint": endpoint,
            "leaves_machine": bool(leaves_machine),
            "input_chars": len(payload),
            "input_sha256_16": _sha(payload),
            **extra,
        }
        t0 = time.perf_counter()
        try:
            yield rec
            rec["ok"] = True
        except Exception as e:  # se registra y se relanza: el ledger no traga errores
            rec["ok"] = False
            rec["error"] = type(e).__name__
            raise
        finally:
            rec["seconds"] = round(time.perf_counter() - t0, 3)
            self.write(rec)

    def summary(self) -> dict:
        """Resumen para la ficha de custodia: llamadas por motor y cuántas salieron."""
        by_engine: dict[str, dict] = {}
        if self.to_stdout or not self.path.is_file():
            return by_engine
        for line in self.path.read_text(encoding="utf-8").splitlines():
            r = json.loads(line)
            e = by_engine.setdefault(r["engine"], {"calls": 0, "left_machine": 0, "chars": 0, "actors": set()})
            e["calls"] += 1
            e["chars"] += r.get("input_chars", 0)
            e["actors"].add(r.get("actor", "unknown"))
            if r.get("leaves_machine"):
                e["left_machine"] += 1
        for e in by_engine.values():
            e["actors"] = sorted(e["actors"])
        return by_engine


_default: Ledger | None = None


def default() -> Ledger:
    global _default
    if _default is None:
        _default = Ledger()
    return _default


def record_call(step: str, engine: str, endpoint: str, leaves_machine: bool, payload: str, **extra):
    """Atajo sobre el ledger por defecto (ruta de CUSTODIA_LEDGER)."""
    return default().call(step, engine, endpoint, leaves_machine, payload, **extra)


def is_local(url: str) -> bool:
    """Heurística para leaves_machine: localhost, 127.x, ::1 o red privada."""
    from urllib.parse import urlparse
    host = (urlparse(url).hostname or "").lower()
    return host in {"localhost", "127.0.0.1", "::1"} or host.startswith(("127.", "10.", "192.168."))
