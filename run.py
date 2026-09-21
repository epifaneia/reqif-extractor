"""run — orquestador del pipeline completo: s1→s2→s3→s4→s5→s6.

Recorre los PDFs de data/inputs (o los que matcheen un substring) y procesa los
documentos EN PARALELO (config.DOC_WORKERS hilos): cada documento avanza sus
pasos en secuencia como subproceso (mismo CLI que a mano). El paso s1 (docling)
es pesado en CPU/RAM, así que se limita con un semáforo a config.S1_CONCURRENCY
ejecuciones concurrentes; el resto de pasos (red/API) puede ir más paralelo.

Robustez: si un paso falla para un documento, se aborta el resto de pasos de
ESE documento y los demás siguen; al final se imprime un resumen con los fallos
y un WARNING por cada documento que acabe con 0 requisitos.

Uso:
  python run.py            # todos los PDFs de data/inputs
  python run.py AUTOSAR_SWS_COM    # solo los PDFs cuyo nombre contenga ese texto

Notas:
  · s1 filtra por nombre de PDF; s2–s6 filtran por stem slug — run.py pasa a cada
    paso el argumento adecuado para seleccionar SOLO el documento en curso.
  · s3/s4 llaman a Gemini (gastan API); s1/s2/s5/s6 no.
  · La salida de cada documento se imprime en bloque al terminar (no se
    entremezclan líneas de documentos distintos).
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

PIPELINE = ROOT / "pipeline"
STEPS = [
    ("s1_docling", "PDF → markdown + regiones (docling)"),
    ("s2_headers", "markdown → headers"),
    ("s3_requirements", "headers+md → requisitos (Gemini)"),
    ("s4_vision", "regiones → delta (gmft + crops + fallback)"),
    ("s5_merge", "A ∪ B → merged.json"),
    ("s5b_positions", "auditoría posicional vs PDF (orden+jerarquía)"),
    ("s5c_infoguard", "blindaje information-only (md ↔ merged)"),
    ("s6_build_reqif", "merged.json → .reqif"),
]

# docling es pesado (CPU/RAM): como mucho S1_CONCURRENCY a la vez.
_S1_SEM = threading.Semaphore(max(1, config.S1_CONCURRENCY))
_PRINT_LOCK = threading.Lock()


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _run_step(script: str, arg: str) -> tuple[bool, str]:
    """Lanza pipeline/<script>.py <arg>. Devuelve (ok, salida)."""
    env = dict(os.environ, PYTHONIOENCODING="utf-8")
    try:
        proc = subprocess.run(
            [sys.executable, str(PIPELINE / f"{script}.py"), arg],
            capture_output=True, text=True, encoding="utf-8", env=env,
            cwd=str(ROOT), timeout=5400,
        )
    except subprocess.TimeoutExpired:
        return False, "TIMEOUT (90 min)"
    out = (proc.stdout or "") + (("\n" + proc.stderr) if proc.stderr.strip() else "")
    return proc.returncode == 0, out.strip()


def _merged_req_count(stem: str) -> int | None:
    """Nº de requisitos del merged.json del stem, o None si no existe/no parsea."""
    p = config.MERGED_JSON_DIR / f"{stem}.json"
    if not p.is_file():
        return None
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        return len(data.get("requisitos", []))
    except Exception:
        return None


def _process_doc(pdf: Path) -> dict:
    """Corre los 6 pasos de UN documento. Nunca lanza: los fallos se reportan.

    Devuelve {"stem", "lines" (salida en bloque), "failure" (o None)}.
    """
    stem = _slug(pdf.stem)
    lines: list[str] = [f"### {pdf.name}  (stem: {stem})"]
    failure: tuple[str, str, str] | None = None
    try:
        for script, desc in STEPS:
            # s1 selecciona por nombre de PDF; el resto por stem slug.
            arg = pdf.name if script == "s1_docling" else stem
            t0 = time.time()
            if script == "s1_docling":
                with _S1_SEM:
                    ok, out = _run_step(script, arg)
            else:
                ok, out = _run_step(script, arg)
            dt = time.time() - t0
            status = "OK " if ok else "FAIL"
            lines.append(f"  [{status}] {script:18s} {desc:36s} ({dt:5.1f}s)")
            for line in out.splitlines():
                lines.append(f"         | {line}")
            if not ok:
                failure = (stem, script, out.splitlines()[-1] if out else "sin salida")
                lines.append(f"         ! abortando pasos restantes de {stem}")
                break
    except Exception as e:  # noqa: BLE001 — un doc roto no tumba a los demás
        failure = (stem, "run", f"excepción del orquestador: {e}")
        lines.append(f"  [FAIL] excepción inesperada: {e}")
    return {"stem": stem, "lines": lines, "failure": failure}


def main() -> int:
    # --- cable 1: identidad. El pipeline no corre de forma anónima. ---
    from custodia.identity import require_actor, MissingActor
    try:
        actor = require_actor()
    except MissingActor as e:
        print(f"run: {e}")
        return 2
    print(f"run: actor {actor}")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    pdfs = sorted(config.INPUTS_DIR.glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if only.lower() in p.name.lower()
                or only.lower() in _slug(p.stem).lower()]
    if not pdfs:
        print(f"No hay PDFs en {config.INPUTS_DIR}" + (f" que matcheen '{only}'" if only else ""))
        return 1

    workers = max(1, min(config.DOC_WORKERS, len(pdfs)))
    print(f"== run: {len(pdfs)} documento(s), {len(STEPS)} pasos, "
          f"{workers} doc(s) en paralelo (docling máx {max(1, config.S1_CONCURRENCY)}) ==")
    failures: list[tuple[str, str, str]] = []   # (doc, paso, error)
    done_stems: list[str] = []
    t_total = time.time()

    with ThreadPoolExecutor(max_workers=workers) as ex:
        futures = [ex.submit(_process_doc, pdf) for pdf in pdfs]
        for fut in as_completed(futures):
            r = fut.result()
            with _PRINT_LOCK:
                print()
                for line in r["lines"]:
                    print(line)
            if r["failure"]:
                failures.append(r["failure"])
            else:
                done_stems.append(r["stem"])

    print(f"\n== resumen ({time.time() - t_total:.1f}s) ==")
    # Aviso 0 reqs: un doc que "completó" pero acaba vacío casi siempre es un problema.
    for stem in sorted(done_stems):
        if _merged_req_count(stem) == 0:
            print(f"  [WARNING] {stem}: 0 requisitos, revisar")
    if failures:
        print(f"  {len(failures)} documento(s) con fallo:")
        for stem, script, err in sorted(failures):
            print(f"  ✗ {stem} en {script}: {err[:160]}")
        return 1
    print(f"  ✓ {len(pdfs)} documento(s) completaron los {len(STEPS)} pasos.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
