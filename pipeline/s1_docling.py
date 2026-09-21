"""s1 — PDF → markdown con Docling (+ regiones figura/tabla, v5).

Reusa clients/docling_adapter. Entrada: data/inputs/*.pdf →
  · data/interim/markdown/<stem>.md            (idéntico a v4)
  · data/interim/regions/<stem>/manifest.json  (v5: crops de PictureItem/TableItem)
  · data/interim/regions/<stem>/R####_p<pág>_<tipo>.png

El manifest lleva por región: id, type (picture|table), page, bbox (puntos,
origen arriba-izquierda) y png; más page_headers (título de sección → página)
para que s4 asigne parent_header de forma determinista.

- stem = slug del nombre del PDF (alfanumérico + "_").
- Si el texto embebido sale casi vacío (PDF escaneado), reintenta con OCR.
- --regions-only: NO escribe el markdown (deja intactos md/json_a existentes);
  solo regenera los crops. Útil para validar v5 sin re-pagar s3.

Uso:
  python pipeline/s1_docling.py                          # todos los PDFs
  python pipeline/s1_docling.py AUTOSAR_SWS_COM                  # solo los que contengan ese texto
  python pipeline/s1_docling.py AUTOSAR_SWS_COM --regions-only   # solo crops, md intacto
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from clients.docling_adapter import convert_pdf_with_regions, markdown_from_pdf  # noqa: E402

_MIN_CHARS = 200  # por debajo de esto, asumimos que no hubo texto y probamos OCR


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _save_regions(stem: str, source_file: str, reg: dict) -> Path:
    """Vuelca crops PNG + manifest.json a data/interim/regions/<stem>/."""
    out_dir = config.REGIONS_DIR / stem
    out_dir.mkdir(parents=True, exist_ok=True)
    # limpiar crops de corridas anteriores (regiones renumeradas)
    for old in out_dir.glob("R*.png"):
        old.unlink()
    entries = []
    for r in reg["regions"]:
        png = f"{r['id']}_p{r['page']}_{r['type']}.png"
        r["image"].save(out_dir / png)
        entries.append({"id": r["id"], "type": r["type"], "page": r["page"],
                        "bbox": r["bbox"], "png": png})
    manifest = {
        "stem": stem,
        "source_file": source_file,
        "pages": reg["pages"],
        "regions": entries,
        "page_headers": reg["page_headers"],
        "skipped_small": reg["skipped_small"],
    }
    mpath = out_dir / "manifest.json"
    mpath.write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8")
    return mpath


def convert_one(pdf: Path, *, regions_only: bool = False) -> dict:
    stem = _slug(pdf.stem)
    out = config.MARKDOWN_DIR / f"{stem}.md"
    emit_regions = config.REGIONS_EMIT or regions_only

    if emit_regions:
        md, reg = convert_pdf_with_regions(
            pdf, pdf_ocr=False,
            regions_scale=config.REGIONS_SCALE,
            crop_min_px=config.CROP_MIN_PX,
            crop_pad_px=config.CROP_PAD_PX,
        )
    else:
        md, reg = markdown_from_pdf(pdf, pdf_ocr=False), None

    ocr_used = False
    if len(md.strip()) < _MIN_CHARS:
        if emit_regions:
            md, reg = convert_pdf_with_regions(
                pdf, pdf_ocr=True,
                regions_scale=config.REGIONS_SCALE,
                crop_min_px=config.CROP_MIN_PX,
                crop_pad_px=config.CROP_PAD_PX,
            )
        else:
            md = markdown_from_pdf(pdf, pdf_ocr=True)
        ocr_used = True

    if not regions_only:
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(md, encoding="utf-8")

    n_reg = {"picture": 0, "table": 0}
    if reg is not None:
        _save_regions(stem, pdf.name, reg)
        for r in reg["regions"]:
            n_reg[r["type"]] += 1

    return {"pdf": pdf.name, "stem": stem, "chars": len(md), "ocr": ocr_used,
            "out": out, "regions": n_reg, "regions_emitted": reg is not None,
            "md_written": not regions_only}


def main() -> int:
    args = [a for a in sys.argv[1:]]
    regions_only = "--regions-only" in args
    args = [a for a in args if a != "--regions-only"]
    only = args[0] if args else None

    pdfs = sorted(config.INPUTS_DIR.glob("*.pdf"))
    if only:
        pdfs = [p for p in pdfs if only.lower() in p.name.lower()]
    if not pdfs:
        print("No hay PDFs que convertir.")
        return 1
    for pdf in pdfs:
        try:
            r = convert_one(pdf, regions_only=regions_only)
            tag = " (OCR)" if r["ocr"] else ""
            reg_tag = ""
            if r["regions_emitted"]:
                reg_tag = f"  [{r['regions']['picture']} fig + {r['regions']['table']} tab]"
            md_tag = "" if r["md_written"] else "  (md intacto)"
            print(f"  {r['pdf'][:50]:50s} → {r['stem']}.md  ({r['chars']:>7d} chars){tag}{reg_tag}{md_tag}")
        except Exception as e:
            print(f"  {pdf.name[:50]:50s} → ERROR: {e}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
