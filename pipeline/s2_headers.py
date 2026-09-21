"""s2 — markdown → headers (pasada A, determinista).

Reusa utils/md_headers.parse_headers. Lee data/interim/markdown/<stem>.md y
escribe data/interim/json_a/<stem>.json con la parte `headers`. s3 lo completará
con `requisitos` (y posibles headers_adicionales del LLM).

Uso:
  python pipeline/s2_headers.py            # todos los .md
  python pipeline/s2_headers.py AUTOSAR_SWS_COM    # solo los que contengan ese texto
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402
from utils.md_headers import parse_headers  # noqa: E402


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    mds = sorted(config.MARKDOWN_DIR.glob("*.md"))
    if only:
        mds = [m for m in mds if only.lower() in m.stem.lower()]
    if not mds:
        print("No hay markdown en data/interim/markdown/. ¿Corriste s1?")
        return 1
    for md in mds:
        stem = md.stem
        headers = parse_headers(md.read_text(encoding="utf-8"))
        out = config.JSON_A_DIR / f"{stem}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        # No pisar `requisitos` si s3 ya escribió algo: cargamos y mezclamos.
        obj = {}
        if out.is_file():
            try:
                obj = json.loads(out.read_text(encoding="utf-8"))
            except Exception:
                obj = {}
        obj["stem"] = stem
        obj["headers"] = headers
        out.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        levels = sorted({h["level"] for h in headers})
        print(f"  {stem[:50]:50s} → {len(headers):3d} headers  niveles={levels}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
