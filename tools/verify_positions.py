"""Auditor de POSICIONES: orden y jerarquía del merged.json contra el PDF.

El orden de import en Polarion sale del campo `pos` (s5) y la jerarquía de
`parent_header`. Ambos son aproximaciones (página del crop, criterio del
modelo en el rescate...). Este auditor los verifica contra la única fuente de
verdad determinista: LA POSICIÓN FÍSICA en el PDF.

  posición(req) = (página, offset) de la 1ª aparición del req_id en el texto
  del PDF (pdfium, forma normalizada — inmune a espacios/escapes garbled).

Chequeos:
  ORDEN     la secuencia de reqs por `pos` debe ser monótona en (pág, offset).
  JERARQUÍA el parent de un req debe ser el último header que empieza antes
            de su posición (mismo criterio que un lector humano).
  FANTASMAS req_id no localizable en el PDF de un doc no-invisible → posible
            alucinación del extractor → flag (nunca auto-borrado).

Modos:
  python tools/verify_positions.py            # --check de todos los merged
  python tools/verify_positions.py AUTOSAR_SWS_COM    # --check de uno
  python tools/verify_positions.py --fix      # re-estampa pos y re-ancla
                                              # parents INCONSISTENTES; luego
                                              # re-corre s6 para regenerar reqif
Salida --check: exit 0 si no hay violaciones (usable en CI).

Límites honestos: en docs clase-invisible (IDs solo en píxeles) no hay nada
que localizar — si <50% de los ítems son localizables, el doc se reporta como
"no auditable" y NO se toca (el orden de la visión de v4 se respeta).
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from utils.ids import norm_id  # noqa: E402


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _find_pdf(stem: str) -> Path | None:
    for p in config.INPUTS_DIR.glob("*.pdf"):
        if _slug(p.stem) == stem:
            return p
    return None


def _norm_label(s: str) -> str:
    # sin backslashes: los headings del md llevan escapes ("CYS\_a92ee0") y los
    # títulos del manifest no — sin esto el matching de headers se cae al 5-20%
    return re.sub(r"\s+", " ", (s or "").replace("\\", "")).strip().lower()


def _page_texts_norm(pdf: Path) -> list[str]:
    """Texto de cada página en forma normalizada (norm_id: sin espacios)."""
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(str(pdf))
    try:
        return [norm_id(doc[i].get_textpage().get_text_range()) for i in range(len(doc))]
    finally:
        doc.close()


def _locate(needle_norm: str, pages: list[str]) -> tuple[int, int] | None:
    """(página 1-based, offset) de la 1ª aparición de la forma normalizada."""
    if not needle_norm:
        return None
    for i, ptxt in enumerate(pages):
        off = ptxt.find(needle_norm)
        if off >= 0:
            return (i + 1, off)
    return None


def _header_positions(merged: dict, manifest: dict | None,
                      pages: list[str]) -> list[tuple[tuple[int, int], str]]:
    """[(posición, anchor)] de cada header, ordenado por posición.

    Página: del manifest (docling la reporta directa — fiable); offset: buscando
    el título normalizado dentro de esa página (0 si no aparece literal).
    """
    label_to_anchor: dict[str, str] = {}
    for h in merged.get("headers", []):
        a = h.get("anchor")
        if not a:
            continue
        for key in ((h.get("raw") or ""),
                    f"{(h.get('number') or '').strip()} {(h.get('title') or '').strip()}",
                    (h.get("title") or "")):
            k = _norm_label(key)
            if k and k not in label_to_anchor:
                label_to_anchor[k] = a

    out: list[tuple[tuple[int, int], str]] = []
    seen: set[str] = set()
    for ph in (manifest or {}).get("page_headers", []):
        anchor = label_to_anchor.get(_norm_label(ph.get("text") or ""))
        if not anchor or anchor in seen:
            continue
        page = int(ph.get("page") or 0)
        if not (1 <= page <= len(pages)):
            continue
        tnorm = norm_id(ph.get("text") or "")[:60]
        off = pages[page - 1].find(tnorm) if tnorm else -1
        out.append(((page, max(0, off)), anchor))
        seen.add(anchor)
    out.sort(key=lambda t: t[0])
    return out


def _parent_for(pos: tuple[int, int],
                hpos: list[tuple[tuple[int, int], str]]) -> str | None:
    best = None
    for p, a in hpos:
        if p <= pos:
            best = a
        else:
            break
    return best


def audit_one(stem: str, fix: bool = False) -> dict | None:
    mpath = config.MERGED_JSON_DIR / f"{stem}.json"
    if not mpath.is_file():
        return None
    merged = json.loads(mpath.read_text(encoding="utf-8"))
    reqs = merged.get("requisitos", [])
    if not reqs:
        return {"stem": stem, "n": 0, "auditable": False}
    pdf = _find_pdf(stem)
    if pdf is None:
        return {"stem": stem, "n": len(reqs), "auditable": False, "note": "sin PDF"}

    pages = _page_texts_norm(pdf)
    man_path = config.REGIONS_DIR / stem / "manifest.json"
    manifest = json.loads(man_path.read_text(encoding="utf-8")) if man_path.is_file() else None
    hpos = _header_positions(merged, manifest, pages)

    # Solo los ítems TEXTUALES son localizables por diseño; los de crop/
    # fallback vienen de PÍXELES (pdfium no puede verlos) → no son fantasmas.
    _IMG_SOURCES = {"crop", "vision", "fallback"}
    pages_loose = [re.sub(r"[-_]", "", t) for t in pages]  # 2º intento: sin guiones
    located: dict[int, tuple[int, int]] = {}   # idx req -> (pág, offset)
    img_src: set[int] = set()
    for i, r in enumerate(reqs):
        if str(r.get("source") or "") in _IMG_SOURCES:
            img_src.add(i)
        rid = norm_id(str(r.get("req_id") or ""))
        p = _locate(rid, pages)
        if p is None and rid:
            p = _locate(re.sub(r"[-_]", "", rid), pages_loose)  # md con guiones mutilados
        if p:
            located[i] = p

    textual = [i for i in range(len(reqs)) if i not in img_src]
    coverage = (len([i for i in textual if i in located]) / len(textual)) if textual else 0.0
    if coverage < 0.5:
        # clase invisible (o casi): nada fiable que auditar — no tocar
        return {"stem": stem, "n": len(reqs), "auditable": False,
                "coverage": coverage, "note": "clase-invisible: se respeta el orden de visión"}

    # ORDEN: violaciones = pares consecutivos localizados que van hacia atrás
    order_viol = 0
    prev = None
    for i in sorted(located, key=lambda i: reqs[i].get("pos", i)):
        cur = located[i]
        if prev is not None and cur < prev:
            order_viol += 1
        prev = cur

    # JERARQUÍA: parent actual vs parent por posición física. Solo se evalúa
    # si el mapa de headers es DENSO (≥80% de los headers del merged tienen
    # posición): con un mapa pobre, "el último header antes de X" apunta a un
    # ancestro grueso y todo parecería violación (artefacto del auditor).
    n_headers = len(merged.get("headers", []))
    header_cov = len(hpos) / n_headers if n_headers else 0.0
    parent_viol = []
    if header_cov >= 0.8:
        for i, p in located.items():
            want = _parent_for(p, hpos)
            have = reqs[i].get("parent_header")
            if want is not None and have != want:
                parent_viol.append((i, have, want))

    # FANTASMAS: solo ítems de fuente TEXTUAL no localizables (los de imagen
    # son ilocalizables por naturaleza, no sospechosos).
    ghosts = [reqs[i]["req_id"] for i in textual if i not in located]

    fixed = 0
    if fix and (order_viol or parent_viol):
        for i, _have, want in parent_viol:
            reqs[i]["parent_header"] = want
            fixed += 1
        # re-estampar pos: localizados por posición física; no-localizados
        # conservan su orden relativo actual, intercalados tras su predecesor.
        order_now = sorted(range(len(reqs)), key=lambda i: reqs[i].get("pos", i))
        keyed = []
        last_key = (0, -1)
        for i in order_now:
            if i in located:
                last_key = located[i]
            keyed.append((last_key, len(keyed), i))  # estable para no-localizados
        keyed.sort(key=lambda t: (t[0], t[1]))
        for new_pos, (_k, _s, i) in enumerate(keyed):
            reqs[i]["pos"] = new_pos
        merged["requisitos"] = sorted(reqs, key=lambda r: r["pos"])
        mpath.write_text(json.dumps(merged, ensure_ascii=False, indent=2), encoding="utf-8")

    return {"stem": stem, "n": len(reqs), "auditable": True, "coverage": coverage,
            "header_cov": header_cov, "n_img": len(img_src),
            "order_viol": order_viol, "parent_viol": len(parent_viol),
            "ghosts": ghosts, "fixed": fixed}


def main() -> int:
    args = [a for a in sys.argv[1:]]
    fix = "--fix" in args
    args = [a for a in args if a != "--fix"]
    only = args[0] if args else None

    merged_files = sorted(config.MERGED_JSON_DIR.glob("*.json"))
    if only:
        merged_files = [m for m in merged_files if only.lower() in m.stem.lower()]
    if not merged_files:
        print("No hay merged.json. ¿Corriste s5?")
        return 1

    total_viol = 0
    for m in merged_files:
        r = audit_one(m.stem, fix=fix)
        if r is None:
            continue
        if not r.get("auditable"):
            print(f"  {r['stem'][:52]:52s} · {r['n']:4d} reqs · NO AUDITABLE "
                  f"({r.get('note', 'sin ítems')})")
            continue
        viol = r["order_viol"] + r["parent_viol"]
        total_viol += 0 if fix else viol
        gh = len(r["ghosts"])
        jtag = (f"jerarquía {r['parent_viol']}" if r["header_cov"] >= 0.8
                else f"jerarquía n/a (headers loc. {100*r['header_cov']:.0f}%)")
        line = (f"  {r['stem'][:52]:52s} · {r['n']:4d} reqs "
                f"({r['n_img']} de imagen) · loc. texto {100*r['coverage']:.0f}% · "
                f"orden {r['order_viol']} · {jtag} · fantasmas {gh}")
        if fix and r["fixed"]:
            line += f" · CORREGIDOS {r['fixed']} (re-corre s6)"
        print(line)
        if gh:
            print(f"    fantasmas (revisar, posible alucinación): {r['ghosts'][:6]}"
                  + (" ..." if gh > 6 else ""))
    if fix:
        print("\n--fix aplicado donde hacía falta. Regenera los reqif: python pipeline/s6_build_reqif.py")
        return 0
    print(f"\n{'✓ sin violaciones' if total_viol == 0 else f'✗ {total_viol} violaciones (usa --fix)'}")
    return 0 if total_viol == 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
