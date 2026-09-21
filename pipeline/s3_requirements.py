"""s3 — markdown + headers → requisitos (pasada A). Llama a Gemini.

Porteo fiel de `_llm_pass` (+ `_validate_extras`, `_audit_extras`, `_merge_extras`,
`_prune_empty_headings`) de un pipeline anterior, con DOS cambios:
  · usa el prompt v3 (prompts/requirements_extraction.txt) → campo `type`.
  · cablea E/S a la estructura nueva: lee markdown + headers de json_a (s2),
    escribe json_a/<stem>.json con headers (merged) + requisitos (con type).

Centinela de salida: en docs muy densos una sola llamada devuelve solo parte de
los ítems, así que se extrae en TANDAS (cada una excluye los req_ids ya vistos)
hasta tanda vacía o config.EXTRACT_MAX_ROUNDS.

Uso:
  python pipeline/s3_requirements.py AUTOSAR_SWS_COM   # un doc (o substring)
  python pipeline/s3_requirements.py           # todos los que tengan json_a
"""
from __future__ import annotations

import json
import re
import sys
import time
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from clients.gemini import generate_json as gemini_json  # noqa: E402
from utils.ids import norm_id  # noqa: E402
from utils.md_headers import Header, format_for_prompt  # noqa: E402

import logging
_log = logging.getLogger(__name__)

_SYSTEM_REQS = (config.PROMPTS_DIR / "requirements_extraction.txt").read_text(encoding="utf-8")
_AUDIT_FILE = config.PROMPTS_DIR / "rescue_audit.txt"
_SYSTEM_AUDIT = _AUDIT_FILE.read_text(encoding="utf-8") if _AUDIT_FILE.is_file() else ""

_VALID_TYPES = {"requirement", "information"}


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _source_pdf_name(stem: str) -> str:
    for p in config.INPUTS_DIR.glob("*.pdf"):
        if _slug(p.stem) == stem:
            return p.name
    return f"{stem}.pdf"


# ── ayudantes portados de stage8 ─────────────────────────────────────────────
def _validate_extras(stem: str, raw_extras: Any, parser_headers: list[Header]) -> list[dict]:
    if not isinstance(raw_extras, list):
        return []
    parser_anchors = {h["anchor"] for h in parser_headers}
    accepted_new: set[str] = set()
    out: list[dict] = []
    for e in raw_extras:
        if not isinstance(e, dict):
            continue
        after = str(e.get("after_anchor") or "").strip()
        new_anchor = str(e.get("new_anchor") or "").strip()
        title = str(e.get("title") or "").strip()
        try:
            level = int(e.get("level"))
        except (TypeError, ValueError):
            continue
        if not (1 <= level <= 6) or not title or not new_anchor:
            continue
        if not new_anchor.startswith("HX"):
            continue
        if new_anchor in parser_anchors or new_anchor in accepted_new:
            continue
        if after not in parser_anchors and after not in accepted_new:
            continue
        out.append({
            "after_anchor": after, "new_anchor": new_anchor, "level": level,
            "number": str(e.get("number") or "").strip(),
            "title": title, "evidencia": str(e.get("evidencia") or "").strip(),
        })
        accepted_new.add(new_anchor)
    return out


def _audit_extras(stem: str, candidates: list[dict], parser_headers: list[Header]) -> list[dict]:
    if not candidates:
        return []
    if not config.AUDIT_EXTRAS or not _SYSTEM_AUDIT.strip() or not config.GEMINI_API_KEY:
        return candidates  # accept-all si no hay auditoría
    tree_block = format_for_prompt(parser_headers) if parser_headers else "(vacío)"
    cand_lines = [
        f"{c['new_anchor']} | level={c['level']} | num={c['number'] or ''} | after={c['after_anchor']}\n"
        f"  title:     {c['title']}\n  evidencia: {c['evidencia']}"
        for c in candidates
    ]
    user_text = ("--- ARBOL DE HEADERS VALIDADOS ---\n" + tree_block +
                 "\n--- CANDIDATOS A AUDITAR ---\n" + "\n".join(cand_lines))
    data = None
    for attempt in range(1, 3):
        try:
            data = gemini_json(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL,
                               user_text=user_text, system_instruction=_SYSTEM_AUDIT, temperature=0.0,
                               timeout_s=config.GEMINI_REQ_TIMEOUT)
            break
        except Exception as e:
            _log.warning("s3 audit %s intento %s: %s", stem, attempt, e)
            if attempt < 2:
                time.sleep(5)
    if not isinstance(data, dict) or not isinstance(data.get("evaluaciones"), list):
        return candidates  # fallback accept-all
    decisions = {}
    for ev in data["evaluaciones"]:
        if isinstance(ev, dict) and ev.get("new_anchor"):
            decisions[str(ev["new_anchor"]).strip()] = str(ev.get("decision") or "").strip().upper()
    return [c for c in candidates if decisions.get(c["new_anchor"]) == "ACCEPT"]


def _merge_extras(base_headers: list[Header], extras: list[dict]) -> list[Header]:
    result: list[Header] = [dict(h) for h in base_headers]
    pending = list(extras)
    while pending:
        progressed = False
        still = []
        for ex in pending:
            idx = next((i for i, h in enumerate(result) if h["anchor"] == ex["after_anchor"]), None)
            if idx is None:
                still.append(ex)
                continue
            result.insert(idx + 1, {
                "anchor": ex["new_anchor"], "level": ex["level"],
                "number": ex.get("number", "") or "", "title": ex["title"],
                "raw": ex.get("evidencia", "") or ex["title"], "parent_anchor": "",
            })
            progressed = True
        pending = still
        if not progressed:
            break
    # Recomponer parent_anchor por stack sobre el level (igual que parse_headers).
    stack: list[Header] = []
    for h in result:
        while stack and stack[-1]["level"] >= h["level"]:
            stack.pop()
        h["parent_anchor"] = stack[-1]["anchor"] if stack else ""
        stack.append(h)
    return result


def _prune_empty_headings(headers: list[Header], requirements: list[dict]) -> list[Header]:
    if not headers:
        return headers
    parent_of = {h["anchor"]: h.get("parent_anchor", "") for h in headers}
    useful: set[str] = set()
    for r in requirements:
        p = r.get("parent_header")
        if p and p in parent_of:
            useful.add(p)
    for anchor in list(useful):
        cur = parent_of.get(anchor, "")
        while cur and cur not in useful:
            useful.add(cur)
            cur = parent_of.get(cur, "")
    return [h for h in headers if h["anchor"] in useful]


# ── pasada LLM ───────────────────────────────────────────────────────────────
_MORE_ROUNDS_SUFFIX = (
    "\n\n--- REQ_IDS YA EXTRAÍDOS EN TANDAS ANTERIORES ({n}) ---\n{ids}\n\n"
    "Los req_ids de arriba YA están extraídos. Devuelve SOLO los ítems taggeados "
    "del documento que NO estén en esa lista, con el mismo esquema JSON. "
    'Si ya no queda ninguno nuevo, devuelve {{"headers_adicionales": [], "requisitos": []}}.'
)


def _call_reqs(stem: str, user_text: str, tag: str):
    """UNA llamada de extracción con reintentos. Devuelve el dict parseado o None."""
    for attempt in range(1, 3):
        try:
            return gemini_json(api_key=config.GEMINI_API_KEY, model=config.GEMINI_MODEL,
                               user_text=user_text, system_instruction=_SYSTEM_REQS, temperature=0.1,
                               timeout_s=config.GEMINI_REQ_TIMEOUT)
        except Exception as e:
            _log.warning("s3 %s %s intento %s: %s", stem, tag, attempt, e)
            if attempt < 2:
                time.sleep(15)
    return None


def _ids_block(ids: list[str]) -> str:
    return "\n".join("  " + ", ".join(ids[i:i + 8]) for i in range(0, len(ids), 8))


def _llm_pass(stem: str, md_text: str, parser_headers: list[Header]) -> tuple[list[Header], list[dict], int]:
    snippet = md_text[: config.GEMINI_REQ_MAX_CHARS]
    headers_block = format_for_prompt(parser_headers) if parser_headers else "(sin encabezados)"
    base_text = ("--- ENCABEZADOS DEL DOCUMENTO ---\n" + headers_block +
                 "\n--- INICIO MARKDOWN ---\n" + snippet + "\n--- FIN MARKDOWN ---")

    # Centinela de salida: en docs muy densos una sola llamada devuelve solo
    # PARTE de los ítems. Se pide en tandas: cada una excluye los req_ids ya
    # vistos (lista que crece); se para con tanda vacía, tanda sin nada nuevo,
    # o al llegar a EXTRACT_MAX_ROUNDS. Dedup por forma canónica (norm_id).
    raw_extras: list = []
    raw_reqs: list[dict] = []
    seen_norm: set[str] = set()
    seen_ids: list[str] = []
    any_ok = False
    rounds = 0
    for rnd in range(1, config.EXTRACT_MAX_ROUNDS + 1):
        rounds = rnd
        user_text = base_text
        if seen_ids:
            user_text += _MORE_ROUNDS_SUFFIX.format(n=len(seen_ids), ids=_ids_block(seen_ids))
        data = _call_reqs(stem, user_text, f"tanda {rnd}")
        if not isinstance(data, dict):
            break  # fallo total de la tanda → conservar lo acumulado
        any_ok = True
        if isinstance(data.get("headers_adicionales"), list):
            raw_extras.extend(data["headers_adicionales"])
        part = data.get("requisitos")
        if not isinstance(part, list) or not part:
            break  # tanda vacía → el modelo no ve nada nuevo
        fresh = 0
        for r in part:
            if not isinstance(r, dict):
                continue
            req_id = str(r.get("req_id") or "").strip()
            texto = str(r.get("texto") or "").strip()
            if not req_id or not texto or norm_id(req_id) in seen_norm:
                continue
            seen_norm.add(norm_id(req_id))
            seen_ids.append(req_id)
            raw_reqs.append(r)
            fresh += 1
        if fresh == 0:
            break  # solo repitió ya-vistos → no insistir

    if not any_ok:
        return parser_headers, [], rounds  # ni una tanda buena: no podar headers

    extras = _audit_extras(stem, _validate_extras(stem, raw_extras, parser_headers), parser_headers)
    merged_headers = _merge_extras(parser_headers, extras)
    all_anchors = {h["anchor"] for h in merged_headers}

    out: list[dict] = []
    for r in raw_reqs:
        req_id = str(r.get("req_id") or "").strip()
        texto = str(r.get("texto") or "").strip()
        parent = r.get("parent_header")
        parent = parent.strip() or None if isinstance(parent, str) else None
        if parent is not None and parent not in all_anchors:
            parent = None
        tipo = str(r.get("type") or "").strip().lower()
        if tipo not in _VALID_TYPES:
            tipo = "requirement"  # default conservador
        out.append({"req_id": req_id, "texto": texto, "type": tipo, "parent_header": parent})

    # NO podar aquí: los headers vacíos en la pasada A pueden tener reqs de la
    # pasada B (visión). La poda se hace en s5, tras fusionar A∪B. Si podáramos
    # aquí, en docs donde docling pierde los IDs (s3=0 reqs) se irían TODOS los
    # headers y los reqs de visión quedarían huérfanos (bug visto en un doc real).
    return merged_headers, out, rounds


def process(stem: str) -> dict:
    md = (config.MARKDOWN_DIR / f"{stem}.md").read_text(encoding="utf-8")
    ja_path = config.JSON_A_DIR / f"{stem}.json"
    base = json.loads(ja_path.read_text(encoding="utf-8")) if ja_path.is_file() else {}
    parser_headers = base.get("headers", [])
    headers, reqs, rounds = _llm_pass(stem, md, parser_headers)
    obj = {
        "stem": stem,
        "source_file": _source_pdf_name(stem),
        "headers": headers,
        "requisitos": reqs,
    }
    ja_path.parent.mkdir(parents=True, exist_ok=True)
    ja_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    n_info = sum(1 for r in reqs if r["type"] == "information")
    return {"stem": stem, "headers": len(headers), "reqs": len(reqs),
            "info": n_info, "req": len(reqs) - n_info, "rounds": rounds}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    jas = sorted(config.JSON_A_DIR.glob("*.json"))
    if only:
        jas = [p for p in jas if only.lower() in p.stem.lower()]
    if not jas:
        print("No hay json_a. ¿Corriste s1 y s2?")
        return 1
    for ja in jas:
        r = process(ja.stem)
        print(f"  {r['stem'][:46]:46s} → {r['reqs']:4d} reqs ({r['req']} req + {r['info']} info) "
              f"· {r['headers']} headers · {r['rounds']} tanda(s)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
