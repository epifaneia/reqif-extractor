"""Detección de IDs huérfanos (red de seguridad v5). Sin API.

El riesgo real del enrutado por regiones no es que el VLM alucine: es que el
layout NO VEA una figura → el crop nunca se genera → requisito perdido EN
SILENCIO. Mitigación determinista: a partir de los IDs ya extraídos se derivan
"familias" (regex generalizando los tramos numéricos) y se barre el texto del
documento buscando cadenas que matcheen una familia pero NO estén capturadas.
Si aparecen huérfanos, el doc cae al fallback de visión completa.

Conservador a propósito: un falso positivo solo cuesta una pasada cara de más;
un falso negativo cuesta un requisito — y eso es fallo de compliance.
"""
from __future__ import annotations

import re

from utils.ids import norm_id

# tope de familias distintas: docs con IDs muy heterogéneos degradarían a un
# barrido casi libre (todo matchea); mejor pocas familias fiables.
_MAX_FAMILIES = 40
# una familia debe generalizar al menos este nº de IDs conocidos para usarse
# (families de 1 solo ID suelen ser ruido del extractor).
_MIN_FAMILY_SIZE = 2


def _family_pattern(req_id: str) -> str | None:
    """Generaliza un ID a su familia: tramos de dígitos → \\d+.

    'SWS_Com_00117' → 'SWS_Com_\\d+' · 'OEM-Sec_53a3c0_1015' →
    'CYS\\-ERTESec_53a3c0_\\d+' (el hex/las letras quedan literales).
    Devuelve None si el ID no tiene ningún tramo numérico (no generaliza).
    """
    rid = (req_id or "").strip()
    if not rid or not re.search(r"\d", rid) or len(rid) < 4:
        return None
    pat = re.escape(rid)
    pat = re.sub(r"\d+", r"\\d+", pat)
    return pat


def id_families(known_ids: set[str]) -> list[re.Pattern]:
    """Compila las familias de los IDs conocidos (las más pobladas primero)."""
    counts: dict[str, int] = {}
    for rid in known_ids:
        pat = _family_pattern(rid)
        if pat:
            counts[pat] = counts.get(pat, 0) + 1
    fams = [p for p, n in sorted(counts.items(), key=lambda kv: -kv[1])
            if n >= _MIN_FAMILY_SIZE][:_MAX_FAMILIES]
    # \b no funciona tras '-' escapado en todos los casos; usamos lookarounds
    # de no-alfanumérico para delimitar el match completo.
    return [re.compile(r"(?<![A-Za-z0-9])" + p + r"(?![A-Za-z0-9])") for p in fams]


# líneas de HEADING markdown ("## 5.2 Título - GUID: X"): su GUID es un título
# de sección, no un requisito. OJO: las líneas "GUID: X / CR n" SUELTAS no se
# excluyen — en los docs de algunos OEM esa sintaxis anota tanto títulos como REQUISITOS
# (bug aprendido con AUTOSAR_SWS_COM: excluirlas ocultaba 433 requisitos reales). El
# árbitro es el rescate de texto (s4c'), que tiene el md como contexto.
_GUID_LINE_RE = re.compile(r"^#{1,6}\s")

# Anotación del OEM: "GUID: <id>" (a veces "... / CR nnn") en línea NO-heading.
# En estos docs CADA requisito viene anotado así → escáner directo, sin
# depender de familias (los GUIDs hex no generalizan bien por regex).
# Charset acotado (sin . , ; ( ) |): corta solo el token-ID aunque la "línea"
# sea una fila entera de tabla md con prosa alrededor. Admite espacios sueltos
# dentro (IDs garbled tipo 'CYS- HSM_SP...'); norm_id los colapsa al comparar.
_ANNOT_RE = re.compile(r"GUID\s*:\s*([A-Za-z0-9\\][A-Za-z0-9_\\\- ]{2,60})")


def find_annotated(text: str, known_ids: set[str]) -> list[str]:
    """IDs anotados 'GUID: X' que NO están capturados. Barre TODAS las líneas.

    Complementa a find_orphans (los GUIDs hex no generalizan por familia).
    OJO: se barren también los headings md — docling promociona los bloques de
    requisito del OEM a heading ('## Título - GUID: X'), así que "es heading" NO
    implica "es estructura" (caso AUTOSAR_SWS_COM: 553 GUID-headings, solo 125 son
    capítulos reales). La separación la hace la PERTENENCIA (los GUIDs de los
    headers de json_a ya están en known) y el árbitro final es el rescate de
    texto, que descarta títulos de sección con el md delante.
    """
    from utils.ids import clean_id
    known_norm = {norm_id(i) for i in known_ids}
    out: set[str] = set()
    for m in _ANNOT_RE.finditer(text):
        cand = clean_id(m.group(1)).strip()
        cand = re.sub(r"\s+GUID$", "", cand)  # artefacto: anotaciones seguidas
        if len(cand) < 4 or not re.search(r"\d", cand):
            continue
        if norm_id(cand) not in known_norm:
            out.add(cand)
    return sorted(out)


def find_orphans(text: str, known_ids: set[str],
                 family_ids: set[str] | None = None) -> list[str]:
    """IDs que matchean una familia conocida pero NO están capturados.

    `text` = md de la pasada A + tablas gmft reconstruidas (todo lo que las
    rutas baratas han VISTO). `known_ids` = capturados (para la pertenencia);
    `family_ids` = de dónde derivar las familias (por defecto known_ids —
    pásale SOLO req_ids limpios: derivar de GUIDs compuestos de headers genera
    familias-ruido tipo 'GUID: X / CR n'). Devuelve huérfanos únicos ordenados.
    """
    fams = id_families(family_ids if family_ids is not None else known_ids)
    if not fams:
        return []
    known_norm = {norm_id(i) for i in known_ids}
    orphans: set[str] = set()
    for line in text.splitlines():
        # las líneas de heading/anotación GUID-CR no contienen requisitos
        if _GUID_LINE_RE.search(line):
            continue
        for fam in fams:
            for m in fam.finditer(line):
                cand = m.group(0)
                if norm_id(cand) not in known_norm:
                    orphans.add(cand)
    return sorted(orphans)
