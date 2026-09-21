"""s5b — auditoría/corrección POSICIONAL del merged.json (v5). Sin API.

Envoltorio de pipeline para tools/verify_positions: tras el merge (s5) y antes
de construir el reqif (s6), verifica contra el PDF (fuente de verdad física)
que el ORDEN de los requisitos es monótono y que cada uno cuelga del capítulo
correcto — y lo CORRIGE de forma determinista si no. Así el orden de import en
Polarion queda garantizado por código, no por revisión manual.

Los docs clase-invisible (IDs solo en píxeles) no son auditables: se respeta
el orden que trajo la visión (comportamiento v4). Los "fantasmas" (IDs de
fuente textual no localizables en el PDF) se REPORTAN, nunca se borran.

Uso:
  python pipeline/s5b_positions.py AUTOSAR_SWS_COM   # un doc (substring)
  python pipeline/s5b_positions.py           # todos los merged
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tools"))

from verify_positions import audit_one  # noqa: E402
import config  # noqa: E402


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    merged = sorted(config.MERGED_JSON_DIR.glob("*.json"))
    if only:
        merged = [m for m in merged if only.lower() in m.stem.lower()]
    if not merged:
        print("No hay merged.json. ¿Corriste s5?")
        return 1
    for m in merged:
        r = audit_one(m.stem, fix=True)
        if r is None:
            continue
        if not r.get("auditable"):
            print(f"  {r['stem'][:52]:52s} → no auditable ({r.get('note', 'sin ítems')})")
            continue
        gh = len(r.get("ghosts", []))
        print(f"  {r['stem'][:52]:52s} → orden {r['order_viol']} · "
              f"jerarquía {r['parent_viol']} · corregidos {r['fixed']}"
              + (f" · [WARNING] {gh} fantasmas (revisar routes/informe)" if gh else ""))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
