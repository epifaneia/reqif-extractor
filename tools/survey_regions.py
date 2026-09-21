"""Sondeo Fase 0 (v5) — ¿de dónde venía lo que rescataba la visión cara de v4?

Cruza los json_b de v4 (congelados en data/interim/json_b_v4_baseline) contra
las rutas baratas de v5, SIN gastar API:

  · in_tables : el ID aparece en las tablas que gmft reconstruye del vector
                → recuperable GRATIS por s4a.
  · in_md     : el ID está en el markdown de la pasada A (docling lo vio;
                s3 lo perdió) → lo cazan la ruta de texto o el regex de
                huérfanos (s4c).
  · figure    : no está ni en md ni en tablas → tiene que salir de los crops
                de figuras (s4b) o del fallback.

Si además ya existe un json_b NUEVO (s4 v5 corrido), compara recall directo:
IDs del baseline recuperados vs perdidos, con desglose por ruta.

Uso:
  python tools/survey_regions.py            # todos los docs del baseline
  python tools/survey_regions.py AUTOSAR_SWS_COM    # solo los que contengan ese texto
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
import config  # noqa: E402
from utils.ids import norm_id  # noqa: E402

BASELINE_DIR = config.JSON_A_DIR.parent / "json_b_v4_baseline"
CACHE_DIR = config.REGIONS_DIR  # tablas gmft cacheadas junto a los crops


def _gmft_md_cached(stem: str) -> str:
    """Tablas gmft del doc (md concatenado), cacheadas en regions/<stem>/."""
    cache = CACHE_DIR / stem / "tables_gmft.md"
    if cache.is_file():
        return cache.read_text(encoding="utf-8")
    pdf = None
    import re as _re
    def _slug(name: str) -> str:
        return _re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")
    for p in config.INPUTS_DIR.glob("*.pdf"):
        if _slug(p.stem) == stem:
            pdf = p
            break
    if pdf is None:
        return ""
    try:
        from s4_vision import _gmft_tables
        md = "\n\n".join(f"[pág {t['page']}]\n{t['md']}" for t in _gmft_tables(pdf))
    except Exception as e:  # noqa: BLE001
        print(f"    (gmft falló para {stem}: {e})")
        return ""
    cache.parent.mkdir(parents=True, exist_ok=True)
    cache.write_text(md, encoding="utf-8")
    return md


def survey_one(stem: str) -> dict | None:
    bl_path = BASELINE_DIR / f"{stem}.json"
    if not bl_path.is_file():
        return None
    baseline = json.loads(bl_path.read_text(encoding="utf-8"))
    old_items = baseline.get("requisitos", [])
    if not old_items:
        return {"stem": stem, "n_old": 0}

    md_path = config.MARKDOWN_DIR / f"{stem}.md"
    md = md_path.read_text(encoding="utf-8") if md_path.is_file() else ""
    md_norm = norm_id(md)  # colapsa espacios/escapes para buscar IDs "garbled"
    tables_md = _gmft_md_cached(stem)
    tables_norm = norm_id(tables_md)

    buckets = {"in_tables": [], "in_md": [], "figure": []}
    for it in old_items:
        rid = str(it.get("req_id") or "").strip()
        if not rid:
            continue
        rid_n = norm_id(rid)
        if rid_n and rid_n in tables_norm:
            buckets["in_tables"].append(rid)
        elif rid_n and rid_n in md_norm:
            buckets["in_md"].append(rid)
        else:
            buckets["figure"].append(rid)

    # comparación dinámica si hay json_b nuevo (distinto del baseline)
    new_path = config.JSON_B_DIR / f"{stem}.json"
    dyn = None
    if new_path.is_file():
        new = json.loads(new_path.read_text(encoding="utf-8"))
        if "routes" in new:  # marca de v5
            new_ids = {norm_id(str(r.get("req_id") or "")) for r in new.get("requisitos", [])}
            ja = json.loads((config.JSON_A_DIR / f"{stem}.json").read_text(encoding="utf-8"))
            a_ids = {norm_id(str(r.get("req_id") or "")) for r in ja.get("requisitos", [])}
            old_ids = {norm_id(str(r.get("req_id") or "")) for r in old_items}
            covered = old_ids & (new_ids | a_ids)
            missed = sorted(old_ids - new_ids - a_ids)
            dyn = {"covered": len(covered), "missed": missed,
                   "new_total": len(new.get("requisitos", [])),
                   "routes": new.get("routes", {})}
    return {"stem": stem, "n_old": len(old_items), "buckets": buckets, "dyn": dyn}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if not BASELINE_DIR.is_dir():
        print(f"No existe el baseline {BASELINE_DIR} — congela json_b de v4 primero.")
        return 1
    stems = sorted(p.stem for p in BASELINE_DIR.glob("*.json"))
    if only:
        stems = [s for s in stems if only.lower() in s.lower()]
    tot = {"old": 0, "tables": 0, "md": 0, "figure": 0, "covered": 0, "missed": 0}
    any_dyn = False
    for stem in stems:
        r = survey_one(stem)
        if r is None:
            continue
        if r["n_old"] == 0:
            print(f"  {stem[:52]:52s} · v4-visión: 0 ítems (nada que sondear)")
            continue
        b = r["buckets"]
        tot["old"] += r["n_old"]
        tot["tables"] += len(b["in_tables"])
        tot["md"] += len(b["in_md"])
        tot["figure"] += len(b["figure"])
        print(f"  {stem[:52]:52s} · v4-visión: {r['n_old']:3d} → "
              f"tabla-gmft {len(b['in_tables']):3d} · md {len(b['in_md']):3d} · "
              f"figura {len(b['figure']):3d}")
        if r["dyn"]:
            any_dyn = True
            d = r["dyn"]
            tot["covered"] += d["covered"]
            tot["missed"] += len(d["missed"])
            rt = d["routes"]
            print(f"  {'':52s}   v5: cubre {d['covered']}/{r['n_old']} del baseline "
                  f"[tabla {rt.get('table', 0)} · crop {rt.get('crop', 0)} · "
                  f"rescate {rt.get('rescue', 0)} · fallback {rt.get('fallback', 0)}]"
                  + (f" · PERDIDOS: {d['missed']}" if d["missed"] else " · 0 perdidos ✓"))
    n = max(1, tot["old"])
    print(f"\n== sondeo global: {tot['old']} ítems v4-visión → "
          f"{tot['tables']} ({100*tot['tables']//n}%) recuperables por gmft GRATIS · "
          f"{tot['md']} ({100*tot['md']//n}%) visibles en md · "
          f"{tot['figure']} ({100*tot['figure']//n}%) solo-figura ==")
    if any_dyn:
        print(f"== recall v5 vs baseline: {tot['covered']} cubiertos, {tot['missed']} perdidos ==")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
