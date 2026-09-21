"""tools/signoff.py — firmar y verificar salidas. Ver custodia/cli.py y docs/INTEGRATION.md."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from custodia.cli import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
