"""Recorta el prefijo de los IDs de instancia al CÓDIGO corto del documento.

Por qué: el prefijo por defecto es el slug completo del PDF
(`AUTOSAR-SWS-COM_SO-R-0001`).
Polarion a veces LIMITA la longitud del identificador que guarda para el
round-trip ReqIF; si lo trunca, dos documentos podrían volver a colisionar.
Este script recorta el prefijo a solo el código (`SWSCOM_SO-R-0001`),
manteniendo la unicidad entre documentos (los códigos son distintos) y
reescribiendo también las SPEC-OBJECT-REF para que las referencias resuelvan.

Genera copias en data/outputs/reqif/<mode>_shortid/ (NO toca los originales).

Uso:  python tools/crop_ids.py
"""
from __future__ import annotations

import re
import sys
from pathlib import Path
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

REQIF = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
def Q(t: str) -> str: return f"{{{REQIF}}}{t}"
INST = (Q("REQ-IF-HEADER"), Q("SPECIFICATION"), Q("SPEC-OBJECT"), Q("SPEC-HIERARCHY"))
CODE_RE = re.compile(r"^([A-Z]{2,6}\d{3,}|SDV-?\d+|[A-Z]{2,}\d{3,})")


def short_code(filename: str) -> str:
    m = CODE_RE.match(filename)
    return m.group(1) if m else filename.split("_", 1)[0]


def _unique_codes(filenames: list[str]) -> dict[str, str]:
    """Mapa nombre_fichero -> código corto ÚNICO dentro del lote. Si dos documentos
    comparten código (p.ej. `DOC1234_..._2.pdf` y `DOC1234_..._3.pdf`), al 2º/3º se
    le añade sufijo `-2`, `-3`... para no acabar con IDs de instancia idénticos entre
    documentos (= el bug de Polarion 'Unresolved parent key null')."""
    seen: dict[str, int] = {}
    out: dict[str, str] = {}
    for name in filenames:
        base = short_code(name)
        seen[base] = seen.get(base, 0) + 1
        out[name] = base if seen[base] == 1 else f"{base}-{seen[base]}"
    return out


def _crop(idval: str, code: str) -> str:
    """`SLUG_SUFFIX` -> `code_SUFFIX` (el SLUG es la parte antes del 1er '_';
    los sufijos canónicos HDR-/SPEC-/SO-/SH- no llevan '_')."""
    if "_" in idval:
        return f"{code}_{idval.split('_', 1)[1]}"
    return idval


def process(src_dir: Path, dst_dir: Path) -> None:
    files = sorted(src_dir.glob("*.reqif"))
    if not files:
        print(f"  (sin reqif en {src_dir})")
        return
    dst_dir.mkdir(parents=True, exist_ok=True)
    codes = _unique_codes([f.name for f in files])
    if len(set(codes.values())) != len(set(short_code(f.name) for f in files)):
        print("  [aviso] códigos repetidos en el lote: desambiguados con sufijo -2/-3 "
              "para mantener IDs únicos entre documentos")
    for f in files:
        code = codes[f.name]
        tree = etree.parse(str(f))
        root = tree.getroot()
        n = 0
        for e in root.iter():
            if e.tag in INST and e.get("IDENTIFIER"):
                e.set("IDENTIFIER", _crop(e.get("IDENTIFIER"), code))
                n += 1
        for ref in root.iter(Q("SPEC-OBJECT-REF")):
            if ref.text:
                ref.text = _crop(ref.text, code)
        tree.write(str(dst_dir / f.name), xml_declaration=True, encoding="utf-8")
        print(f"  {f.name[:42]:42s} prefijo → {code}_  ({n} ids)")


def main() -> int:
    base = config.OUTPUT_REQIF_DIR
    for mode in ("v1_nested", "v2_flat"):
        src = base / mode
        dst = base / f"{mode}_shortid"
        print(f"== {mode} → {dst.name} ==")
        process(src, dst)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
