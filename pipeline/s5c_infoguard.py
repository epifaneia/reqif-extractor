"""s5c_infoguard — blindaje '(information only)': reconciliación md ↔ merged.

DETERMINISTA, sin API. El extractor (s3) clasifica cada ítem como
type=requirement|information, pero suele PERDER el marcador textual
'(information only)' de la fuente, y hasta ahora s6 ignoraba `type`: el
carácter informativo desaparecía del .reqif. El blindaje tiene dos mitades:

  · s6 (parcheado) renderiza el marcador desde `type`:
        <xhtml:h3>GUID (information only)</xhtml:h3>
    — convención del OEM, pegado al ID. Los campos AD-FullID/AD-ReqID quedan
    LIMPIOS (el marcador jamás contamina el ID: rompería trazabilidad).
  · esta pasada garantiza que `type` es FIEL A LA FUENTE antes de s6:

  1. Si el markdown de docling marca el ítem — marcador a ≤120 chars tras el
     GUID, con los escapes de docling deshechos ('\\_'→'_') — y el extractor
     lo dejó como requirement → se corrige a information (+reporte). La
     fuente manda.
  2. Si type == information sin marcador localizable en fuente → se respeta
     (juicio del LLM sobre NOTES/referencias sin tag); solo cuenta en el
     reporte.

Edita merged.json IN PLACE (backup <stem>.pre_infoguard.json si no existe).
Re-ejecutable: idempotente.

Entrada:  config.MERGED_JSON_DIR/<stem>.json + config.MARKDOWN_DIR/<stem>*.md
Salida:   el mismo merged.json, con `type` reconciliado

Uso:  python pipeline/s5c_infoguard.py [substring]
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import config  # noqa: E402

_MARKER = re.compile(r"\binformation\s+only\b", re.IGNORECASE)
_WINDOW = 120   # chars tras el GUID donde buscar el marcador (línea típica del OEM:
                # '## GUID: CYS-XXX_1 / CR 2084180 (information only)')


def _unescape_md(md: str) -> str:
    """Deshace los escapes de docling ('CYS-HSMECS\\_d83da0\\_2' → con '_')."""
    return re.sub(r"\\(?=[_*#\-.~])", "", md)


def _find_md(stem: str) -> Path | None:
    for pattern in (f"{stem}.md", f"{stem}*.md", f"*{stem}*.md"):
        hits = sorted(config.MARKDOWN_DIR.glob(pattern)) or \
               sorted(config.MARKDOWN_DIR.rglob(pattern))
        if hits:
            return hits[0]
    return None


def _marked_in_source(rid: str, md: str) -> bool:
    for m in re.finditer(re.escape(rid), md):
        if _MARKER.search(md[m.end():m.end() + _WINDOW]):
            return True
    return False


def process(merged_path: Path) -> dict:
    data = json.loads(merged_path.read_text(encoding="utf-8"))
    stem = data.get("stem") or merged_path.stem
    md_path = _find_md(stem)
    stats = {"stem": stem, "total": len(data.get("requisitos", [])),
             "flips": [], "info_total": 0, "info_sin_marcador_fuente": 0,
             "md": md_path.name if md_path else None}
    md = _unescape_md(md_path.read_text(encoding="utf-8", errors="replace")) if md_path else ""

    changed = False
    for r in data.get("requisitos", []):
        marked = bool(md) and _marked_in_source(r.get("req_id", ""), md)
        if marked and r.get("type") != "information":
            r["type"] = "information"
            stats["flips"].append(r.get("req_id"))
            changed = True
        if r.get("type") == "information":
            stats["info_total"] += 1
            if md and not marked:
                stats["info_sin_marcador_fuente"] += 1

    if changed:
        backup = merged_path.with_suffix(".pre_infoguard.bak")
        if not backup.exists():
            backup.write_bytes(merged_path.read_bytes())
        merged_path.write_text(json.dumps(data, ensure_ascii=False, indent=2),
                               encoding="utf-8")
    return stats


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    merged = sorted(config.MERGED_JSON_DIR.glob("*.json"))
    merged = [p for p in merged if not p.name.endswith(".bak")]
    if only:
        merged = [p for p in merged if only.lower() in p.stem.lower()]
    if not merged:
        print(f"s5c: no hay merged.json en {config.MERGED_JSON_DIR}"
              + (f" que matcheen '{only}'" if only else ""))
        return 1

    for path in merged:
        st = process(path)
        extra = f", {len(st['flips'])} corregidos a information" if st["flips"] else ""
        warn = " [SIN MD: solo type del LLM]" if st["md"] is None else ""
        print(f"s5c: {st['stem']} — {st['info_total']}/{st['total']} information"
              f" ({st['info_sin_marcador_fuente']} sin marcador en fuente){extra}{warn}")
        for rid in st["flips"]:
            print(f"     ↷ {rid}: la fuente dice '(information only)' → type corregido")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
