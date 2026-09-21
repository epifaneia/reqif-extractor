"""Parser determinista de encabezados de Markdown post-Docling.

Produce un árbol con anchors estables para alimentar Stage 8 (ReqIF jerárquico).
La jerarquía se infiere del **prefijo numérico** del título cuando existe
(``2.1.1`` → nivel 3), porque Docling tiende a aplanar todos los headers a
``##`` en documentos PDF/Word, sin preservar la profundidad real.

Headers que son anclas de requisito (``## GUID: CYS-...``) se omiten:
ya los recogerá el extractor de requisitos como ``req_id``.
"""

from __future__ import annotations

import re
from typing import TypedDict


class Header(TypedDict):
    anchor: str          # ID estable, "H001", "H002", ...
    level: int           # nivel jerárquico inferido (1..N)
    number: str          # prefijo numérico ("2.1.1") o "" si no hay
    title: str           # título limpio, sin número ni sufijo GUID
    raw: str             # línea completa tras los #, para trazabilidad
    parent_anchor: str   # anchor del padre, o "" si raíz


# ATX: 1-6 hashes, espacio, texto. Cerramos eventuales hashes finales opcionales.
_HEADER_RE = re.compile(r"^(#{1,6})\s+(.+?)\s*#*\s*$")

# Prefijo numérico al inicio del título: "2", "2.1", "10.5.3", etc.
_NUM_PREFIX_RE = re.compile(r"^(\d+(?:\.\d+)*)(?:\s+|$)")

# Sufijo " - GUID: XXX" o variantes — lo quitamos del title visible.
_GUID_SUFFIX_RE = re.compile(r"\s*[-–—]\s*GUID:\s*\S+.*$", re.IGNORECASE)

# Pseudo-headers que son anclas de requisito, no capítulos.
_GUID_ONLY_RE = re.compile(r"^\s*GUID\s*:", re.IGNORECASE)

# Fenced code blocks: ``` o ~~~ (con lenguaje opcional).
_FENCE_RE = re.compile(r"^\s*(```|~~~)")


def parse_headers(md_text: str) -> list[Header]:
    """Extrae el árbol de encabezados de un documento Markdown.

    Reglas:
    - Skip líneas dentro de bloques de código (```...``` o ~~~...~~~).
    - Skip headers cuyo título empiece por "GUID:" (anclas de requisito).
    - Si el título lleva prefijo numérico (``3.4.1 Foo``), nivel = nº de
      segmentos del prefijo. Si no, nivel = número de ``#``.
    - El parent se asigna por stack: el header más reciente con level < actual.

    Args:
        md_text: contenido completo del .md.

    Returns:
        Lista de Header en orden de aparición.
    """
    headers: list[Header] = []
    stack: list[Header] = []  # camino de ancestros (level estrictamente creciente)
    in_fence = False
    counter = 0

    for line in md_text.splitlines():
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if in_fence:
            continue

        m = _HEADER_RE.match(line)
        if not m:
            continue

        hashes, raw_title = m.group(1), m.group(2).strip()
        if not raw_title or _GUID_ONLY_RE.match(raw_title):
            continue

        # Sacar prefijo numérico si está al inicio.
        num_match = _NUM_PREFIX_RE.match(raw_title)
        if num_match:
            number = num_match.group(1)
            title_no_num = raw_title[num_match.end():].strip()
            level = number.count(".") + 1
        else:
            number = ""
            title_no_num = raw_title
            level = len(hashes)

        # Quitar el sufijo " - GUID: XXX" del título visible.
        title = _GUID_SUFFIX_RE.sub("", title_no_num).strip()
        if not title:
            # Header sin contenido útil tras limpiar — usa el raw.
            title = raw_title

        # Asignar parent: pop del stack hasta encontrar uno con nivel menor.
        while stack and stack[-1]["level"] >= level:
            stack.pop()
        parent_anchor = stack[-1]["anchor"] if stack else ""

        counter += 1
        header: Header = {
            "anchor": f"H{counter:04d}",
            "level": level,
            "number": number,
            "title": title,
            "raw": raw_title,
            "parent_anchor": parent_anchor,
        }
        headers.append(header)
        stack.append(header)

    return headers


def format_for_prompt(headers: list[Header]) -> str:
    """Renderiza los headers para incluirlos en el user_text del LLM.

    Formato compacto: ``H001 [L1] 2 Hardware`` — anchor, nivel, número, título.
    Un anchor por línea, sin árbol indentado (el LLM ve el level explícito).
    """
    lines = []
    for h in headers:
        num = f" {h['number']}" if h["number"] else ""
        lines.append(f"{h['anchor']} [L{h['level']}]{num} {h['title']}")
    return "\n".join(lines)
