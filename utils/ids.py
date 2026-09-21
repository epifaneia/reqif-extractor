"""IDs de requisito: limpieza y forma canónica. Compartido por s3/s4/s5.

La forma canónica (`norm_id`) se usa SOLO para comparar/dedup; en los outputs
el req_id viaja verbatim tal y como lo devolvió el extractor.
"""
from __future__ import annotations

import re


def clean_id(s: str) -> str:
    """Quita escapes de markdown (\\_ -> _, backslashes sueltos) y recorta."""
    return (s or "").replace("\\", "").strip()


def norm_id(s: str) -> str:
    """Forma canónica para comparar IDs: sin backslashes ni espacios internos."""
    return re.sub(r"\s+", "", clean_id(s))
