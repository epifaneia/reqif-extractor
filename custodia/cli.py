"""signoff CLI — firmar, verificar y resumir custodia. Tercer cable, a mano.

    python tools/signoff.py sign   <fichero>        # crea <fichero>.signoff.json con el actor actual
    python tools/signoff.py verify <fichero>...     # 0 si todos verifican; 1 si alguno no
    python tools/signoff.py ledger [ruta]           # resumen del libro de custodia

Necesita CUSTODIA_ACTOR (o un proveedor registrado) y CUSTODIA_SIGNING_KEY
(o un esquema registrado). Un fichero sin manifiesto válido NO debe importarse
ni enviarse: es lo que el flujo de aprobación de la empresa enchufa aquí.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path


def main(argv: list[str] | None = None) -> int:
    from custodia import identity, ledger, signoff
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] not in {"sign", "verify", "ledger"}:
        print(__doc__)
        return 2
    cmd, args = argv[0], argv[1:]
    if cmd == "sign":
        if len(args) != 1:
            print(__doc__); return 2
        actor = identity.current_actor()
        out = signoff.sign(Path(args[0]), actor)
        print(f"signoff: {out.name} firmado por {actor}")
        return 0
    if cmd == "verify":
        if not args:
            print(__doc__); return 2
        rc = 0
        for a in args:
            ok, why = signoff.verify(Path(a))
            print(f"{'ok  ' if ok else 'FAIL'} {Path(a).name}: {why}")
            rc |= 0 if ok else 1
        return rc
    if cmd == "ledger":
        led = ledger.Ledger(args[0]) if args else ledger.default()
        s = led.summary()
        if not s:
            print("ledger vacío o inexistente"); return 0
        print(json.dumps(s, ensure_ascii=False, indent=2))
        return 0
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
