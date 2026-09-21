"""Wrapper de Docling para conversión PDF/DOCX → Markdown.

Configuración conservadora de memoria (batch_size=1 por tipo) para evitar
OOM en documentos grandes con tablas y OCR.
"""

from __future__ import annotations

import logging
from pathlib import Path

_log = logging.getLogger(__name__)


def markdown_from_pdf(path: Path, *, pdf_ocr: bool = False) -> str:
    """Convierte un PDF (o chunk de PDF) a Markdown via Docling."""
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        TableFormerMode,
        TableStructureOptions,
        ThreadedPdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    pipeline_opts = ThreadedPdfPipelineOptions(
        ocr_batch_size=1,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=8,
        images_scale=0.5,
        generate_page_images=False,
        generate_picture_images=False,
        do_ocr=pdf_ocr,
        force_backend_text=(not pdf_ocr),
        table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST),
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
    )
    _log.info("Docling (PDF): %s", path.name)
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def convert_pdf_with_regions(
    path: Path,
    *,
    pdf_ocr: bool = False,
    regions_scale: float = 2.0,
    crop_min_px: int = 48,
    crop_pad_px: int = 8,
) -> tuple[str, dict]:
    """Convierte un PDF a Markdown Y extrae las regiones figura/tabla (v5).

    UNA sola pasada de docling con LA MISMA configuración que markdown_from_pdf
    (el markdown sale byte-idéntico al de v4; los json_a existentes siguen
    valiendo). Las regiones NO usan generate_picture_images: se renderizan con
    pypdfium2 desde el bbox que reporta el layout (docling pone el layout,
    pdfium los píxeles) → cero coste extra de RAM en docling.

    Devuelve (markdown, regions) donde regions =
      {"pages": n, "regions": [{"id","type","page","bbox","image"(PIL)}...],
       "page_headers": [{"page","text"}...], "skipped_small": n}
    bbox en puntos PDF con origen ARRIBA-IZQUIERDA (ya convertido: el bbox crudo
    de docling viene BOTTOMLEFT — la trampa clásica de offsets).
    """
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        TableFormerMode,
        TableStructureOptions,
        ThreadedPdfPipelineOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from docling_core.types.doc import PictureItem, SectionHeaderItem, TableItem, TitleItem

    pipeline_opts = ThreadedPdfPipelineOptions(
        ocr_batch_size=1,
        layout_batch_size=1,
        table_batch_size=1,
        queue_max_size=8,
        images_scale=0.5,
        generate_page_images=False,
        generate_picture_images=False,
        do_ocr=pdf_ocr,
        force_backend_text=(not pdf_ocr),
        table_structure_options=TableStructureOptions(mode=TableFormerMode.FAST),
    )
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_opts)}
    )
    _log.info("Docling (PDF+regiones): %s", path.name)
    result = converter.convert(str(path))
    doc = result.document
    md = doc.export_to_markdown()

    # --- página → alto en puntos (para invertir el eje Y del bbox) ---
    page_h = {no: p.size.height for no, p in doc.pages.items()}

    raw: list[dict] = []          # regiones con bbox top-left en puntos
    page_headers: list[dict] = []
    for item, _lvl in doc.iterate_items():
        if isinstance(item, (SectionHeaderItem, TitleItem)):
            if item.prov:
                page_headers.append({"page": item.prov[0].page_no,
                                     "text": (item.text or "").strip()})
            continue
        if not isinstance(item, (PictureItem, TableItem)):
            continue
        if not item.prov:
            continue
        prov = item.prov[0]
        page = prov.page_no
        h = page_h.get(page)
        if h is None:
            continue
        bb = prov.bbox
        # BOTTOMLEFT → TOPLEFT: y' = alto_página - y  (el bug de offsets del research)
        top, bottom = h - bb.t, h - bb.b
        if bottom < top:
            top, bottom = bottom, top
        raw.append({
            "type": "table" if isinstance(item, TableItem) else "picture",
            "page": page,
            "bbox": [float(bb.l), float(top), float(bb.r), float(bottom)],
        })

    # --- render de crops con pypdfium2, una página renderizada UNA vez ---
    import pypdfium2 as pdfium

    regions: list[dict] = []
    skipped = 0
    if raw:
        pdf = pdfium.PdfDocument(str(path))
        try:
            by_page: dict[int, list[dict]] = {}
            for r in raw:
                by_page.setdefault(r["page"], []).append(r)
            n = 0
            for page_no in sorted(by_page):
                pil_page = pdf[page_no - 1].render(scale=regions_scale).to_pil()
                for r in by_page[page_no]:
                    l, t, rr, b = (v * regions_scale for v in r["bbox"])
                    l = max(0, l - crop_pad_px); t = max(0, t - crop_pad_px)
                    rr = min(pil_page.width, rr + crop_pad_px)
                    b = min(pil_page.height, b + crop_pad_px)
                    if (rr - l) < crop_min_px or (b - t) < crop_min_px:
                        skipped += 1
                        continue
                    n += 1
                    regions.append({
                        "id": f"R{n:04d}", "type": r["type"], "page": page_no,
                        "bbox": [round(v, 1) for v in r["bbox"]],
                        "image": pil_page.crop((int(l), int(t), int(rr), int(b))),
                    })
        finally:
            pdf.close()

    return md, {
        "pages": len(page_h),
        "regions": regions,
        "page_headers": page_headers,
        "skipped_small": skipped,
    }


def markdown_from_docx(path: Path) -> str:
    """Convierte un DOCX a Markdown via Docling."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    _log.info("Docling (DOCX): %s", path.name)
    result = converter.convert(str(path))
    return result.document.export_to_markdown()


def markdown_from_pptx(path: Path) -> str:
    """Convierte un PPTX/PPT a Markdown via Docling."""
    from docling.document_converter import DocumentConverter

    converter = DocumentConverter()
    _log.info("Docling (PPTX): %s", path.name)
    result = converter.convert(str(path))
    return result.document.export_to_markdown()
