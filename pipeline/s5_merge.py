"""s5 — json_a ∪ json_b → merged.json (CONTRATO). DETERMINISTA, sin API.

Política:
  · requisitos = json_a.requisitos + json_b.requisitos, DEDUP por req_id en
    forma canónica (en conflicto GANA json_a; dentro de cada fuente gana la
    primera aparición).
  · headers = json_a.headers (la estructura la fija la pasada A).
  · si no hay json_b para el stem, merged = json_a tal cual.
  · POSICIÓN: estampa en cada header y requisito un entero `pos` = orden
    documental. Los headers de s2 ya vienen en orden de documento (anchors
    secuenciales) → pos = índice en la lista. Los reqs se ordenan por la
    posición de su parent_header y su orden de aparición dentro (huérfanos al
    final). s6 ordena por `pos`; `number` queda solo como etiqueta.

Entrada: data/interim/json_a/<stem>.json (+ json_b/<stem>.json si existe)
Salida:  data/interim/merged_json/<stem>.json
         con {stem, source_file, headers, requisitos}

Uso:
  python pipeline/s5_merge.py            # todos los que tengan json_a
  python pipeline/s5_merge.py AUTOSAR_SWS_COM    # solo los que contengan ese texto
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from utils.ids import norm_id  # noqa: E402


def _stamp_pos(headers: list[dict], requisitos: list[dict]) -> list[dict]:
    """Estampa `pos` (orden documental) en headers y reqs; devuelve reqs ordenados.

    Headers: pos = índice en la lista de s2 (que ya es el orden del documento).
    Reqs: orden estable por (pos del parent_header, orden de aparición); los
    huérfanos (sin parent o parent desconocido) van al final.
    """
    for i, h in enumerate(headers):
        h["pos"] = i
    header_pos = {h.get("anchor"): h["pos"] for h in headers}
    orphan_pos = len(headers)
    ordered = sorted(requisitos,
                     key=lambda r: header_pos.get(r.get("parent_header"), orphan_pos))
    for i, r in enumerate(ordered):
        r["pos"] = i
    return ordered


def _prune_empty_headings(headers: list[dict], requisitos: list[dict]) -> list[dict]:
    """Quita headers sin ningún req en su subárbol (tras fusionar A∪B). Conserva
    los ancestros de un header útil. Hacerlo AQUÍ (no en s3) evita perder la
    estructura en docs donde docling pierde los IDs y solo la visión aporta reqs.
    """
    if not headers:
        return headers
    parent_of = {h.get("anchor"): h.get("parent_anchor", "") for h in headers}
    useful: set[str] = set()
    for r in requisitos:
        p = r.get("parent_header")
        if p and p in parent_of:
            useful.add(p)
    for anchor in list(useful):
        cur = parent_of.get(anchor, "")
        while cur and cur not in useful:
            useful.add(cur)
            cur = parent_of.get(cur, "")
    return [h for h in headers if h.get("anchor") in useful]


def merge_one(stem: str) -> dict:
    ja = json.loads((config.JSON_A_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    jb_path = config.JSON_B_DIR / f"{stem}.json"
    jb = json.loads(jb_path.read_text(encoding="utf-8")) if jb_path.is_file() else {}

    seen: set[str] = set()  # formas canónicas (norm_id); el req_id sale verbatim
    requisitos: list[dict] = []
    n_a = n_b = dup = 0
    for source, reqs in (("a", ja.get("requisitos", [])), ("b", jb.get("requisitos", []))):
        for r in reqs:
            if not isinstance(r, dict):
                continue
            rid = str(r.get("req_id") or "").strip()
            if not rid or norm_id(rid) in seen:
                dup += 1 if rid else 0
                continue
            seen.add(norm_id(rid))
            requisitos.append(r)
            if source == "a":
                n_a += 1
            else:
                n_b += 1

    headers = _prune_empty_headings(ja.get("headers", []), requisitos)
    requisitos = _stamp_pos(headers, requisitos)

    merged = {
        "stem": stem,
        "source_file": ja.get("source_file", "") or jb.get("source_file", ""),
        "headers": headers,
        "requisitos": requisitos,
    }
    out = config.MERGED_JSON_DIR / f"{stem}.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"stem": stem, "from_a": n_a, "from_b": n_b, "dups": dup,
            "total": len(requisitos), "headers": len(merged["headers"]),
            "has_b": jb_path.is_file(), "out": out}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    jas = sorted(config.JSON_A_DIR.glob("*.json"))
    if only:
        jas = [p for p in jas if only.lower() in p.stem.lower()]
    if not jas:
        print("No hay json_a que mergear. ¿Corriste s1–s3?")
        return 1
    for ja in jas:
        try:
            r = merge_one(ja.stem)
        except Exception as e:  # noqa: BLE001 — un doc malo no para el batch
            print(f"  {ja.stem[:46]:46s} → ERROR: {e}")
            continue
        b_tag = f"+ {r['from_b']} de B" if r["has_b"] else "(sin json_b)"
        dup_tag = f", {r['dups']} dups descartados" if r["dups"] else ""
        print(f"  {r['stem'][:46]:46s} → {r['total']:4d} reqs "
              f"({r['from_a']} de A {b_tag}{dup_tag}), {r['headers']} headers")
        if r["total"] == 0:
            print(f"  [WARNING] {r['stem']}: 0 requisitos, revisar")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
