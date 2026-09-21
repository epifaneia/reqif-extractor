"""s4-legacy — visión de PDF COMPLETO (el s4 de v4, preservado como fallback).

En v5 la pasada B se enruta por regiones (s4_vision.py): tablas → gmft,
figuras → crops a VLM. Este módulo conserva ÍNTEGRA la ruta cara y probada
de v4 (PDF entero troceado a visión) con dos usos:

  · red de seguridad: si tras las rutas baratas quedan IDs huérfanos sin
    capturar, el router llama a `recover(stem, known_ids)` para este doc.
  · CLI directo (byte-compatible con el s4 de v4):
      python pipeline/s4_legacy_fullpage.py AUTOSAR_SWS_COM

La lógica (troceo por páginas, tandas centinela, filtros de ruido) es la de
v4 sin cambios semánticos; solo se factoriza `_collect` para poder devolver
los ítems sin escribir json_b.
"""
from __future__ import annotations

import json
import os
import re
import sys
import time
import logging
import tempfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from clients.gemini import generate_json_with_pdf  # noqa: E402
from utils.ids import clean_id as _clean_id, norm_id as _norm_id  # noqa: E402

_log = logging.getLogger(__name__)
_VALID_TYPES = {"requirement", "information"}

SYSTEM_PROMPT = """\
Eres un Ingeniero de Sistemas experto en automoción y ciberseguridad.

Te paso un PDF de una especificación técnica y dos listas:
1. CAPÍTULOS DETECTADOS — árbol del documento, cada capítulo con un anchor estable.
2. REQ_IDS YA EXTRAÍDOS — identificadores que ya tenemos.

Tu trabajo: leer el PDF (incluyendo lo que la conversión a texto pudo perder por
formato/tablas/tamaños de título) y devolver **ítems taggeados que NO estén ya en
REQ_IDS YA EXTRAÍDOS**.

ÍTEM TAGGEADO = par (identificador, texto):
- identificador: código alfanumérico del mismo estilo que los de la lista.
- texto: descripción con sentido. CAPTÚRALO tanto si impone obligación
  (requirement) como si es informativo/recomendación (information).
- El ID puede ir al principio o al final del texto/bullet.

CLASIFICA cada uno con `type`: "requirement" (obligación: shall/must/will, o sentido
obligacional) o "information" (informativo, recomendación "should/may", referencia).

NO devuelvas: IDs que YA están en la lista; referencias cruzadas inline
("see [ID]", "as defined in [ID]"); IDs sin texto; los GUID de los PROPIOS
títulos de capítulo (los capítulos ya están en CAPÍTULOS DETECTADOS); filas de
la tabla "Change History" / release notes (Change Request, Initial Release...).

PARENT_HEADER: asigna el anchor del capítulo más específico bajo el que aparece.
Si ninguno encaja, parent_header=null.

ESQUEMA JSON OBLIGATORIO (solo JSON, sin markdown):
{
  "nuevos": [
    {"req_id": "SWS_Com_00117", "texto": "...", "type": "requirement", "parent_header": "H0010"}
  ]
}
Si no encuentras ninguno nuevo: {"nuevos": []}"""


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


_GUID_RE = re.compile(r"GUID:\s*(.+?)\s*$")
_CHANGE_HISTORY_RE = re.compile(r"change\s*request|initial\s*release|change\s*history", re.I)
# Texto con forma de título de sección ("5.5 HSM with External Flash"): número de
# capítulo + Título corto sin puntuación final. Cubre headings que json_a perdió
# (header garbled) y que la visión re-captura como "information".
_HEADING_SHAPE_RE = re.compile(r"\d{1,2}(\.\d{1,2}){0,4}\s+[A-Z][^.!?\n]{0,80}")


def _norm_label(s: str) -> str:
    """Forma canónica de un título de capítulo: minúsculas, espacios colapsados."""
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def _header_guids(headers: list[dict]) -> set[str]:
    """GUIDs embebidos en header['raw'] (p.ej. '1 Introduction - GUID: X\\_1')."""
    guids: set[str] = set()
    for h in headers:
        m = _GUID_RE.search(h.get("raw") or "")
        if m:
            guids.add(_clean_id(m.group(1)))
    return guids


def _header_labels(headers: list[dict]) -> set[str]:
    """Títulos de capítulo normalizados ('5 Requirements', 'Requirements', ...)."""
    labels: set[str] = set()
    for h in headers:
        num = (h.get("number") or "").strip()
        ttl = (h.get("title") or "").strip()
        if ttl:
            labels.add(_norm_label(ttl))
            if num:
                labels.add(_norm_label(f"{num} {ttl}"))
    return labels


def _find_pdf(stem: str) -> Path | None:
    for p in config.INPUTS_DIR.glob("*.pdf"):
        if _slug(p.stem) == stem:
            return p
    return None


# --- Visión troceada: la accuracy multimodal decae en contextos grandes, así que
# en docs de > CHUNK_PAGES páginas se llama a la visión por rangos con solape. ---

def _page_count(pdf: Path) -> int:
    from pypdf import PdfReader
    return len(PdfReader(str(pdf)).pages)


def _chunk_ranges(n_pages: int, chunk: int, overlap: int) -> list[tuple[int, int]]:
    """Rangos [ini, fin) de `chunk` páginas con `overlap` de solape.

    Ej.: 97 págs, chunk=50, overlap=3 -> [(0,50), (47,97)].
    Si n_pages <= chunk (o parámetros no troceables) -> un único rango entero.
    """
    if n_pages <= chunk or chunk <= 0 or overlap >= chunk:
        return [(0, n_pages)]
    ranges: list[tuple[int, int]] = []
    start = 0
    while True:
        end = min(start + chunk, n_pages)
        ranges.append((start, end))
        if end >= n_pages:
            return ranges
        start = end - overlap


def _write_page_range(pdf: Path, start: int, end: int) -> Path:
    """Escribe un PDF temporal con las páginas [start, end) del original."""
    from pypdf import PdfReader, PdfWriter
    reader = PdfReader(str(pdf))
    writer = PdfWriter()
    for i in range(start, end):
        writer.add_page(reader.pages[i])
    fd, tmp = tempfile.mkstemp(prefix=f"s4_p{start + 1}_{end}_", suffix=".pdf")
    with os.fdopen(fd, "wb") as f:
        writer.write(f)
    return Path(tmp)


def _call_vision(chunk_pdf: Path, user_text: str, tag: str):
    """UNA llamada a la visión con reintentos. Devuelve el dict parseado o None.

    Resiliencia: una llamada larga puede sufrir un corte de red transitorio
    (ConnectionReset). Reintentamos; si falla del todo, devolvemos None y el
    caller sigue (no perder lo ya extraído).
    """
    for attempt in range(1, 4):
        try:
            return generate_json_with_pdf(
                api_key=config.GEMINI_API_KEY,
                model=config.GEMINI_MODEL,
                pdf_path=chunk_pdf,
                user_text=user_text,
                system_instruction=SYSTEM_PROMPT,
                temperature=0.1,
                timeout_s=config.GEMINI_REQ_TIMEOUT,
            )
        except Exception as e:
            _log.warning("s4-legacy %s intento %s: %s", tag, attempt, e)
            if attempt < 3:
                time.sleep(10)
    return None


def _chunk_nuevos(pdf: Path, rng: tuple[int, int], n_pages: int,
                  headers: list[dict], existing_ids: set[str], tag: str) -> tuple[list, int]:
    """Centinela de salida para UN trozo: tandas SECUENCIALES hasta tanda vacía.

    En trozos muy densos el modelo devuelve solo parte de los ítems en una
    llamada. Se itera: cada tanda recibe la lista de ya-vistos (que CRECE con
    los req_id devueltos) y pide solo lo que falte; se para con tanda vacía,
    tanda sin ningún ID nuevo, o al llegar a EXTRACT_MAX_ROUNDS.

    Devuelve (nuevos_crudos, n_tandas). El filtrado de ruido y el dedup global
    entre trozos los hace el caller.
    """
    a, b = rng
    whole = (a, b) == (0, n_pages)
    chunk_pdf = pdf if whole else _write_page_range(pdf, a, b)
    seen_ids = set(existing_ids)
    seen_norm = {_norm_id(i) for i in seen_ids}
    acc: list = []
    rounds = 0
    try:
        for rnd in range(1, config.EXTRACT_MAX_ROUNDS + 1):
            rounds = rnd
            result = _call_vision(chunk_pdf, _build_user_text(headers, seen_ids),
                                  f"trozo {tag} tanda {rnd}")
            part = result.get("nuevos") if isinstance(result, dict) else None
            part = part if isinstance(part, list) else []
            if not part:
                break  # tanda vacía (o fallo total) → no hay más que pedir
            fresh = 0
            for r in part:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("req_id") or "").strip()
                if not rid or _norm_id(rid) in seen_norm:
                    continue
                seen_norm.add(_norm_id(rid))
                seen_ids.add(rid)
                acc.append(r)
                fresh += 1
            if fresh == 0:
                break  # solo repitió ya-vistos → no insistir
    finally:
        if not whole:
            chunk_pdf.unlink(missing_ok=True)
    return acc, rounds


def _vision_nuevos(pdf: Path, headers: list[dict], existing_ids: set[str],
                   n_pages: int) -> tuple[list, int]:
    """Visión troceada: trozos EN PARALELO, tandas secuenciales dentro de cada uno.

    Los trozos son independientes entre sí (I/O de red) → ThreadPoolExecutor.
    Las tandas de un trozo NO se paralelizan: cada una excluye lo que devolvieron
    las anteriores. Devuelve (nuevos_crudos en orden de trozo, n_trozos); el
    dedup/filtrado lo hace el caller.
    """
    ranges = _chunk_ranges(n_pages, config.CHUNK_PAGES, config.CHUNK_OVERLAP)
    n = len(ranges)
    if n == 1:
        nuevos, _ = _chunk_nuevos(pdf, ranges[0], n_pages, headers, existing_ids, "1/1")
        return nuevos, 1

    with ThreadPoolExecutor(max_workers=min(config.S4_CHUNK_WORKERS, n)) as ex:
        futures = [
            ex.submit(_chunk_nuevos, pdf, rng, n_pages, headers, existing_ids, f"{i}/{n}")
            for i, rng in enumerate(ranges, 1)
        ]
        results = [f.result() for f in futures]  # orden = orden de trozo (determinista)

    nuevos: list = []
    for i, ((a, b), (part, rounds)) in enumerate(zip(ranges, results), 1):
        print(f"    · trozo {i}/{n} (págs {a + 1}–{b}) → {len(part)} ítems crudos en {rounds} tanda(s)")
        nuevos.extend(part)
    return nuevos, n


def _build_user_text(headers: list[dict], existing_ids: set[str]) -> str:
    lines = ["--- CAPÍTULOS DETECTADOS (anchor  L  número  título) ---"]
    for h in headers:
        lines.append(f"  {h['anchor']:10s} L{h.get('level',1)} {(h.get('number') or ''):8s} {(h.get('title') or '')[:120]}")
    lines.append("")
    lines.append(f"--- REQ_IDS YA EXTRAÍDOS ({len(existing_ids)}) ---")
    ids = sorted(existing_ids)
    for i in range(0, len(ids), 8):
        lines.append("  " + ", ".join(ids[i:i + 8]))
    lines.append("")
    lines.append("Devuelve los ítems del PDF que NO estén arriba, con el JSON del prompt de sistema.")
    return "\n".join(lines)


def _collect(stem: str, extra_known: set[str] | None = None) -> dict:
    """Corre la visión de PDF completo y devuelve los ítems filtrados SIN escribir.

    `extra_known` (v5): IDs ya capturados por las rutas baratas del router
    (tablas gmft + crops); se excluyen igual que los de la pasada A.
    """
    ja = json.loads((config.JSON_A_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    headers = ja.get("headers", [])
    valid_anchors = {h["anchor"] for h in headers}
    anchor_title = {h["anchor"]: (h.get("title") or "") for h in headers}
    # existing_ids = req_ids de A + GUIDs de los propios capítulos (header['raw']):
    # así la visión no re-captura los headings como "information".
    existing_ids = {str(r.get("req_id")).strip() for r in ja.get("requisitos", []) if r.get("req_id")}
    existing_ids |= _header_guids(headers)
    if extra_known:
        existing_ids |= {i for i in extra_known if i}
    existing_norm = {_norm_id(i) for i in existing_ids}
    heading_labels = _header_labels(headers)

    pdf = _find_pdf(stem)
    if pdf is None:
        return {"stem": stem, "error": "PDF no encontrado en inputs"}

    n_pages = _page_count(pdf)
    nuevos, n_chunks = _vision_nuevos(pdf, headers, existing_ids, n_pages)

    seen_norm = set(existing_norm)
    out: list[dict] = []
    dropped = 0
    for r in nuevos:
        if not isinstance(r, dict):
            continue
        rid = str(r.get("req_id") or "").strip()
        texto = str(r.get("texto") or "").strip()
        if not rid or not texto:
            continue
        # dedup contra req_ids de A + GUIDs de capítulos (forma normalizada)
        if _norm_id(rid) in seen_norm:
            dropped += 1
            continue
        parent = r.get("parent_header")
        parent = parent.strip() or None if isinstance(parent, str) else None
        if parent is not None and parent not in valid_anchors:
            parent = None
        tipo = str(r.get("type") or "").strip().lower()
        if tipo not in _VALID_TYPES:
            tipo = "requirement"
        # ruido: el ítem es un heading re-capturado (texto = título de capítulo,
        # o texto con forma "N.N Título" clasificado como information)
        if _norm_label(texto) in heading_labels or (
                tipo == "information" and _HEADING_SHAPE_RE.fullmatch(texto)):
            dropped += 1
            continue
        # ruido: tabla Change History / release notes
        parent_title = anchor_title.get(parent, "") if parent else ""
        if "change history" in _norm_label(parent_title) or (
                parent is None and _CHANGE_HISTORY_RE.search(texto)):
            dropped += 1
            continue
        out.append({"req_id": rid, "texto": texto, "type": tipo,
                    "parent_header": parent, "source": "vision"})
        seen_norm.add(_norm_id(rid))

    return {"stem": stem, "source_file": ja.get("source_file", ""),
            "existing": len(existing_ids), "requisitos": out, "dropped": dropped,
            "pages": n_pages, "chunks": n_chunks}


def recover(stem: str, known_ids: set[str]) -> list[dict]:
    """Punto de entrada del FALLBACK v5: devuelve ítems nuevos, sin escribir json_b.

    Los ítems salen con source="fallback" para trazabilidad en el merged.
    """
    r = _collect(stem, extra_known=known_ids)
    if "error" in r:
        _log.warning("s4-legacy recover %s: %s", stem, r["error"])
        return []
    for item in r["requisitos"]:
        item["source"] = "fallback"
    return r["requisitos"]


def process(stem: str) -> dict:
    """CLI v4-compatible: corre la visión completa y ESCRIBE json_b/<stem>.json."""
    r = _collect(stem)
    if "error" in r:
        return r
    out = r["requisitos"]
    obj = {"stem": stem, "source_file": r["source_file"],
           "existing": r["existing"], "requisitos": out}
    outp = config.JSON_B_DIR / f"{stem}.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    n_info = sum(1 for x in out if x["type"] == "information")
    return {"stem": stem, "existing": r["existing"], "recovered": len(out),
            "rec_req": len(out) - n_info, "rec_info": n_info, "dropped": r["dropped"],
            "pages": r["pages"], "chunks": r["chunks"]}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if not config.GEMINI_API_KEY:
        print("[ERR] GEMINI_API_KEY no configurada")
        return 1
    jas = sorted(config.JSON_A_DIR.glob("*.json"))
    if only:
        jas = [p for p in jas if only.lower() in p.stem.lower()]
    if not jas:
        print("No hay json_a. ¿Corriste s1–s3?")
        return 1
    for ja in jas:
        r = process(ja.stem)
        if "error" in r:
            print(f"  {ja.stem[:46]:46s} → ERROR: {r['error']}")
            continue
        print(f"  {r['stem'][:46]:46s} → +{r['recovered']:3d} nuevos por visión "
              f"({r['rec_req']} req + {r['rec_info']} info)  "
              f"[{r['pages']} págs en {r['chunks']} trozo(s), "
              f"A+GUIDs: {r['existing']}, ruido filtrado: {r['dropped']}]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
