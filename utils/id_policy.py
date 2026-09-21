"""id_policy — política determinista de req_id para la pasada B. Sin API.

PROBLEMA que resuelve (medido en un doc real de 170 filas de tabla):
  · 167 de 170 ítems de la ruta de tablas llevaban un req_id INVENTADO: el
    prompt pedía "un identificador del estilo de los de la lista", y el modelo
    numeraba las filas (`SWS_Com_00001`…) imitando la convención del documento.
  · Esos IDs falsos COLISIONAN con los reales: 19 números del doc tenían a la
    vez el real (`SWS_Com_007`) y el inventado (`SWS_Com_00007`) con contenido
    distinto. `norm_id` no normaliza ceros, así que ambos llegan al ReqIF como
    `ReqIF.ForeignID` — que es justo el campo que el cliente mapea a OEM REQ ID.
  · Y no eran reproducibles: 49 de 214 textos comunes recibían un ID distinto
    entre dos ejecuciones del mismo pipeline sobre el mismo PDF.

POLÍTICA (en orden de preferencia):
  1. VERBATIM — el ID aparece literal en el markdown del documento o en las
     tablas reconstruidas. Se acepta tal cual. Es el único caso trazable a OEM.
  2. DERIVADO — la fila no tiene ID propio (caso normal en tablas de
     parámetros: la tabla entera lleva uno en el pie, `Table 5: ... [SWS_Com_00347]`).
     Se deriva `SWS_Com_00347.3` del ID de SU tabla + la posición de la fila.
     Visiblemente derivado, estable entre runs y trazable al ID padre real.
  3. ANCLADO A PÁGINA — la tabla no tiene ID propio localizable: `TBL-P31.4`.
     Forma que NO puede confundirse con un OEM REQ ID.
  4. RECHAZO — ID que no es literal del documento ni derivado por nosotros, y
     cuya FORMA no coincide con ninguna de la pasada A. Se descarta y se
     reporta (nunca en silencio).

Nada aquí llama a la API: dado el mismo texto de entrada, la misma salida.
"""
from __future__ import annotations

import re

# Pie de tabla/figura tal y como lo emite docling: '... [SWS_Com_00347]'
_CAPTION_ID_RE = re.compile(r"\[([A-Za-z][A-Za-z0-9](?:[A-Za-z0-9 _.\-]{1,58}))\]\s*$")
_CAPTION_HEAD_RE = re.compile(r"^\s*#{0,4}\s*(Table|Figure|Tabla|Figura)\s+\d+", re.I)
# '<pie>.<fila>' con un '-<n>' opcional de desempate: 'SWS_Com_00347.3',
# 'SWS_Com_00347.3-2', 'TBL-P31.4'. El desempate debe seguir contando como
# derivado — si no, la propia guarda tira ítems que generamos nosotros.
_DERIVED_TAIL_RE = re.compile(r"\.\d+(?:-\d+)?$")
_PAGE_ANCHORED_RE = re.compile(r"^TBL-P\d+\.\d+(?:-\d+)?$")
_TOKEN_RE = re.compile(r"[a-z0-9]+")


def unescape_md(md: str) -> str:
    """Deshace los escapes de docling ('SWS\\_Com' → 'SWS_Com')."""
    return re.sub(r"\\(?=[_*#\-.~])", "", md)


def id_shape(s: str) -> str:
    """Forma canónica de un ID: 'SWS_Com_00117' → 'A-A-#'; 'ISO 11898-6' → 'A #-#'.

    Sirve para aceptar IDs de la familia del documento y rechazar los que solo
    lo parecen (referencias a normas, el propio nombre del doc...).
    """
    out = re.sub(r"[A-Za-z]+", "A", s.strip())
    return re.sub(r"\d+", "#", out)


def shapes_of(ids) -> set[str]:
    return {id_shape(i) for i in ids if i and i.strip()}


def caption_ids(md: str) -> list[tuple[int, str]]:
    """[(nº de línea, id)] de los PIES DE TABLA/FIGURA con ID entre corchetes.

    Solo pies de verdad ('Table 5: ... [SWS_Com_00347]'). El documento anota
    también requisitos sueltos con '[ID]' al final de línea; si los aceptáramos
    como dueños de tabla, una fila acabaría derivando de un requisito vecino en
    vez de su tabla — trazabilidad falsa, que es lo que venimos a arreglar.
    """
    out = []
    for n, line in enumerate(md.splitlines()):
        s = line.rstrip()
        if not _CAPTION_HEAD_RE.match(s):
            continue
        m = _CAPTION_ID_RE.search(s)
        if m:
            out.append((n, m.group(1).strip()))
    return out


def _tokens(s: str) -> set[str]:
    return set(_TOKEN_RE.findall(s.lower()))


def table_rows(table_md: str) -> list[str]:
    """Filas de datos de una tabla markdown (sin cabecera ni separador)."""
    rows = [l for l in table_md.splitlines()
            if l.strip().startswith("|") and set(l.strip()) - set("|-: ")]
    return rows[1:] if len(rows) > 1 else rows


def owner_id(table_md: str, doc_md: str, captions: list[tuple[int, str]],
             lookback: int = 30) -> str | None:
    """ID del pie de LA tabla: localiza su contenido en el md y mira hacia atrás.

    Ancla por la fila de datos más distintiva (la de más tokens) para no
    confundir tablas con cabeceras parecidas (los pies de página del doc).
    """
    if not captions:
        return None
    rows = table_rows(table_md)
    if not rows:
        return None
    anchor = max(rows, key=lambda r: len(_tokens(r)))
    anchor_toks = _tokens(anchor)
    if len(anchor_toks) < 3:
        return None

    lines = doc_md.splitlines()
    best_line, best_score = None, 0.0
    for n, line in enumerate(lines):
        lt = _tokens(line)
        if not lt:
            continue
        score = len(anchor_toks & lt) / len(anchor_toks)
        if score > best_score:
            best_line, best_score = n, score
    if best_line is None or best_score < 0.6:
        return None

    # el pie con ID más cercano por encima (docling lo emite antes de la tabla)
    cands = [(n, cid) for n, cid in captions if best_line - lookback <= n <= best_line + 3]
    if not cands:
        return None
    return min(cands, key=lambda c: abs(c[0] - best_line))[1]


def match_row(texto: str, rows: list[str], min_score: float = 0.35,
              min_shared: int = 3, exclude: set[int] | None = None
              ) -> tuple[int | None, float]:
    """(índice 1-based de la fila que mejor casa con `texto`, score), o (None, 0).

    `exclude` son filas ya asignadas a otro ítem de esta misma tabla: cada fila
    da UN ítem. Sin esto, filas casi gemelas ('RL = 50 Ω' / 'RL = 45 Ω') caen
    todas en el mismo índice y hay que desempatar con sufijos.
    """
    tt = _tokens(texto)
    if not tt:
        return None, 0.0
    ex = exclude or set()
    best_i, best_score = None, 0.0
    for i, row in enumerate(rows, 1):
        if i in ex:
            continue
        rt = _tokens(row)
        if not rt:
            continue
        shared = tt & rt
        if len(shared) < min_shared:
            continue
        score = len(shared) / min(len(tt), len(rt))
        if score > best_score:
            best_i, best_score = i, score
    return (best_i, best_score) if best_score >= min_score else (None, 0.0)


def is_derived(rid: str) -> bool:
    """¿Lo generamos nosotros (regla 2 o 3)? Esos no pasan el filtro de forma."""
    return bool(_PAGE_ANCHORED_RE.match(rid)) or bool(_DERIVED_TAIL_RE.search(rid))


def is_verbatim(rid: str, haystack: str) -> bool:
    return bool(rid) and rid in haystack


def verdict(rid: str, haystack: str, shapes: set[str] | None = None) -> str:
    """'verbatim' | 'derived' | 'alien_shape' | 'reject'.

    OJO: que la FORMA coincida con la familia del documento NO rescata a nadie.
    Ese es justamente el fallo que perseguimos — `SWS_Com_00007` tiene la forma
    perfecta y no existe en el documento. Solo valen literal o derivado.

    La forma se usa en la dirección contraria: un ID que SÍ está en el texto
    pero cuya forma es ajena a la familia del documento (`ISO 11898-6` de una
    referencia normativa, el código del propio nombre del doc) no es un
    identificador de requisito — sale como 'alien_shape' para que el caller
    decida, sin confundirlo con una alucinación.
    """
    if not rid:
        return "reject"
    if is_derived(rid):
        return "derived"
    if not is_verbatim(rid, haystack):
        return "reject"
    if shapes and id_shape(rid) not in shapes:
        return "alien_shape"
    return "verbatim"
