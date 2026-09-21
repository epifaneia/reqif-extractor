"""s4 — pasada B por ENRUTADO DE REGIONES (v5). Sustituye la visión de PDF entero.

La visión de página completa pagaba ~1 €/100 págs re-facturando cada página en
cada tanda y cada solape. v5 colapsa la superficie visual al área real de las
regiones problemáticas (5-10%):

  s4a TABLAS   regiones tabla → gmft reconstruye la retícula desde el vector
               (determinista, CPU) → markdown → UNA serie de llamadas de TEXTO
               baratas con esquema forzado. 0 tokens de visión.
  s4b FIGURAS  crops PNG de PictureItem (manifest de s1) → VLM por lotes con
               responseSchema. Backend intercambiable: gemini | local (vLLM).
               parent_header se asigna DETERMINISTA por página (no lo adivina
               el modelo).
  s4c SEGURIDAD regex de familias de IDs sobre md+tablas: si queda algún ID
               con pinta de requisito NO capturado → fallback al s4 de v4
               (visión de PDF completo, preservado en s4_legacy_fullpage).

Entrada: json_a/<stem>.json + regions/<stem>/manifest.json + PDF de inputs.
Salida:  json_b/<stem>.json (delta) — MISMO contrato que v4; s5/s6 intactos.

Uso:
  python pipeline/s4_vision.py AUTOSAR_SWS_COM   # un doc (substring)
  python pipeline/s4_vision.py           # todos los que tengan json_a
"""
from __future__ import annotations

import json
import logging
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))
import config  # noqa: E402
import s4_legacy_fullpage as legacy  # noqa: E402 — filtros de ruido + fallback
from clients.gemini import generate_json, generate_json_with_images  # noqa: E402
from utils.ids import norm_id as _norm_id  # noqa: E402
from utils.orphans import find_annotated, find_orphans  # noqa: E402
from utils import id_policy  # noqa: E402 — política determinista de req_id

_log = logging.getLogger(__name__)
_VALID_TYPES = {"requirement", "information"}

# ── esquemas de salida (responseSchema Gemini / guided_json vLLM) ───────────
# Planos a propósito: esquemas anidados/complejos degradan la decodificación
# forzada (supresión de tokens en modelos tipo Qwen).
_ITEM_PROPS = {
    "req_id": {"type": "STRING"},
    "texto": {"type": "STRING"},
    "type": {"type": "STRING", "enum": ["requirement", "information"]},
}
TABLE_SCHEMA = {
    "type": "OBJECT",
    "properties": {"requisitos": {"type": "ARRAY", "items": {
        "type": "OBJECT", "properties": dict(_ITEM_PROPS),
        "required": ["req_id", "texto", "type"]}}},
    "required": ["requisitos"],
}
CROP_SCHEMA = {
    "type": "OBJECT",
    "properties": {"items": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"crop": {"type": "INTEGER"}, **_ITEM_PROPS},
        "required": ["crop", "req_id", "texto", "type"]}}},
    "required": ["items"],
}

SYSTEM_TABLES = """\
Eres un Ingeniero de Sistemas experto en automoción y ciberseguridad.

Te paso TABLAS reconstruidas (markdown) de una especificación técnica, cada una
con su página, y la lista de REQ_IDS YA EXTRAÍDOS.

Tu trabajo: devolver los ítems de las tablas que NO estén ya extraídos.
ÍTEM = (req_id, texto): el texto es la fila/celda convertida en frase con sentido.

REGLA CRÍTICA SOBRE req_id — NO INVENTES IDENTIFICADORES.
`req_id` solo se rellena si la PROPIA FILA lleva un identificador escrito
(cópialo EXACTO, carácter a carácter). Si la fila no tiene identificador propio,
devuelve `req_id` como cadena VACÍA "". Es lo normal en tablas de parámetros:
la tabla entera lleva un ID en su pie y las filas no tienen ninguno.
Está PROHIBIDO numerar filas, imitar el formato de la lista de arriba, reutilizar
el ID de la tabla para una fila, o completar ceros. Una cadena vacía es la
respuesta correcta y esperada; un ID inventado corrompe la trazabilidad.

CLASIFICA con `type`: "requirement" (shall/must/will u obligación) o
"information" (informativo, should/may, referencia).

NO devuelvas: IDs ya extraídos; referencias cruzadas inline ("see [ID]");
IDs sin texto; filas de "Change History"/release notes; celdas de cabecera.

Devuelve SOLO JSON: {"requisitos": [{"req_id": "...", "texto": "...", "type": "..."}]}
(`req_id` vale "" cuando la fila no lleva identificador propio.)
Si no hay nada nuevo: {"requisitos": []}"""

SYSTEM_ROWS = """\
Eres un Ingeniero de Sistemas experto en automoción y ciberseguridad.

Te paso FILAS SUELTAS de tablas de una especificación, numeradas. Cada fila es
una celda o conjunto de celdas de una tabla de parámetros.

Tu trabajo: por cada fila que exprese un requisito o una nota informativa,
devolver `texto` = esa fila redactada como UNA frase con sentido, usando las
cabeceras de su tabla para dar contexto (p.ej. fila
'| DC Voltage | time limit = 60 days | -5 | 18 | V |' de la tabla
'Transceiver Absolute Maximum Ratings' →
'DC Voltage for CAN Bus Pins shall be within -5 V and +18 V for a time limit of
60 days.').

NO inventes identificadores: el ID lo pone el sistema a partir de la tabla.
OMITE (no las devuelvas) las filas que sean: cabecera repetida, solo unidades,
separadores, numeración suelta, pies de página del documento, "Change History",
o celdas vacías/sin contenido normativo.

`tabla` y `fila` = los números EXACTOS que te doy. No los cambies.
CLASIFICA con `type`: "requirement" (shall/must/will u obligación) o
"information" (informativo, should/may, referencia, título de tabla).

Devuelve SOLO JSON:
{"filas": [{"tabla": 1, "fila": 3, "texto": "...", "type": "requirement"}]}
Si ninguna fila aporta nada: {"filas": []}"""

ROWS_SCHEMA = {
    "type": "OBJECT",
    "properties": {"filas": {"type": "ARRAY", "items": {
        "type": "OBJECT",
        "properties": {"tabla": {"type": "INTEGER"}, "fila": {"type": "INTEGER"},
                       "texto": {"type": "STRING"},
                       "type": {"type": "STRING",
                                "enum": ["requirement", "information"]}},
        "required": ["tabla", "fila", "texto", "type"]}}},
    "required": ["filas"],
}

SYSTEM_CROPS = """\
Eres un Ingeniero de Sistemas experto en automoción y ciberseguridad.

Te paso RECORTES de figuras/diagramas de una especificación técnica (cada uno
etiquetado con su nº de página y sección). Los diagramas pueden contener
identificadores de requisito (códigos alfanuméricos tipo "CYS-XXX_123",
"SWS_Com_00117", "GUID: ...") con texto asociado.

Tu trabajo: LEER cada recorte y devolver los ítems taggeados visibles.
ÍTEM TAGGEADO = (req_id, texto). Transcribe EXACTAMENTE lo que ves — no
completes, no inventes, no "corrijas" códigos. Si un recorte no contiene
ningún ítem, no devuelvas nada para él.

CLASIFICA con `type`: "requirement" (shall/must/will u obligación) o
"information" (informativo, should/may, leyenda técnica con ID).

NO devuelvas: títulos de capítulo/sección; pies de figura sin ID; logotipos;
filas de "Change History".

`crop` = el número de IMAGEN al que pertenece el ítem.
Devuelve SOLO JSON: {"items": [{"crop": 1, "req_id": "...", "texto": "...", "type": "..."}]}
Si ningún recorte contiene ítems: {"items": []}"""

SYSTEM_RESCUE = """\
Eres un Ingeniero de Sistemas experto en automoción y ciberseguridad.

Te paso EXTRACTOS del markdown de una especificación técnica, su árbol de
CAPÍTULOS (cada uno con un anchor estable) y una lista de IDS A RESOLVER:
identificadores que aparecen en los extractos pero cuyo ítem no fue extraído.

Tu trabajo: pronunciarte sobre CADA ID de la lista, sin excepción:
· Si el ID anota un párrafo/celda/bullet normativo o informativo real →
  devuélvelo en `nuevos` con su texto completo asociado.
· Si el ID es un TÍTULO de capítulo/sección, una referencia cruzada inline
  ("see [ID]"), una fila de "Change History"/release notes, o no tiene texto
  asociado real → devuélvelo en `descartados`.

Un ID que no aparezca en `nuevos` NI en `descartados` se considera SIN
RESOLVER y forzará un reproceso caro del documento: no te dejes ninguno.

CLASIFICA cada nuevo con `type`: "requirement" (shall/must/will u obligación)
o "information" (informativo, should/may, referencia).
PARENT_HEADER: anchor del capítulo más específico bajo el que aparece; null si
ninguno encaja.

Devuelve SOLO JSON:
{"nuevos": [{"req_id": "...", "texto": "...", "type": "...", "parent_header": "H0010"}],
 "descartados": ["ID1", "ID2"]}"""

RESCUE_SCHEMA = {
    "type": "OBJECT",
    "properties": {
        "nuevos": {"type": "ARRAY", "items": {
            "type": "OBJECT",
            "properties": {**_ITEM_PROPS, "parent_header": {"type": "STRING"}},
            "required": ["req_id", "texto", "type"]}},
        "descartados": {"type": "ARRAY", "items": {"type": "STRING"}},
    },
    "required": ["nuevos", "descartados"],
}

# Presupuesto de md de tablas por serie de llamadas. Con data limpia y chunks
# pequeños flash rinde igual que pro; su único fallo observado fue un payload
# de 150k chars (no-JSON). 80k mantiene a flash en su zona cómoda.
_TABLE_BATCH_CHARS = int(os.environ.get("REQIF_TABLE_BATCH_CHARS", "80000"))


def _slug(name: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "_", name).strip("_")


def _find_pdf(stem: str) -> Path | None:
    for p in config.INPUTS_DIR.glob("*.pdf"):
        if _slug(p.stem) == stem:
            return p
    return None


def _load_manifest(stem: str) -> dict | None:
    p = config.REGIONS_DIR / stem / "manifest.json"
    if not p.is_file():
        return None
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except Exception:
        return None


# ── parent_header determinista: página → anchor ──────────────────────────────

def _page_anchor_map(headers: list[dict], page_headers: list[dict]) -> list[tuple[int, str]]:
    """Casa los títulos-por-página del manifest con los anchors de json_a.

    Devuelve [(página, anchor)] ordenado; el anchor de una región es el del
    último header en página <= la suya (aprox. del "capítulo vigente").
    """
    label_to_anchor: dict[str, str] = {}
    for h in headers:
        anchor = h.get("anchor")
        if not anchor:
            continue
        for key in ((h.get("raw") or ""),
                    f"{(h.get('number') or '').strip()} {(h.get('title') or '').strip()}",
                    (h.get("title") or "")):
            # sin backslashes: el md escapea "_" y el manifest no — sin esto
            # el matching se cae al 5-20% y los crops anclan al capítulo gordo
            k = legacy._norm_label(key.replace("\\", ""))
            if k and k not in label_to_anchor:
                label_to_anchor[k] = anchor
    out: list[tuple[int, str]] = []
    for ph in page_headers:
        k = legacy._norm_label((ph.get("text") or "").replace("\\", ""))
        a = label_to_anchor.get(k)
        if a:
            out.append((int(ph.get("page") or 0), a))
    out.sort(key=lambda t: t[0])
    return out


def _anchor_for_page(page_anchors: list[tuple[int, str]], page: int) -> str | None:
    best = None
    for p, a in page_anchors:
        if p <= page:
            best = a
        else:
            break
    return best


# ── s4a: tablas → gmft → texto barato ────────────────────────────────────────

def _df_to_md(df) -> str:
    """DataFrame → tabla markdown (sin depender de tabulate)."""
    def cell(v) -> str:
        s = "" if v is None else str(v)
        if s in ("nan", "None"):
            s = ""
        return s.replace("\n", " ").replace("|", "/").strip()
    cols = [cell(c) for c in df.columns]
    lines = ["| " + " | ".join(cols) + " |",
             "|" + " --- |" * len(cols)]
    for _, row in df.iterrows():
        lines.append("| " + " | ".join(cell(v) for v in row) + " |")
    return "\n".join(lines)


def _gmft_tables(pdf: Path) -> list[dict]:
    """Reconstruye las tablas del PDF con gmft (TATR sobre texto vectorial).

    Devuelve [{"page": 1-based, "md": "..."}]. Lanza si gmft no está usable:
    el caller degrada esa ruta (los crops de tabla van a visión).
    """
    from gmft.auto import AutoTableDetector, AutoTableFormatter
    from gmft.pdf_bindings import PyPDFium2Document

    detector = AutoTableDetector()
    formatter = AutoTableFormatter()
    doc = PyPDFium2Document(str(pdf))
    out: list[dict] = []
    try:
        cropped = []
        for page in doc:
            cropped.extend(detector.extract(page))
        for ct in cropped:
            page_no = int(getattr(getattr(ct, "page", None), "page_number", 0)) + 1
            try:
                df = formatter.extract(ct).df()
            except Exception as e:  # una tabla rota no tumba las demás
                _log.warning("gmft: tabla en pág %s ilegible: %s", page_no, e)
                continue
            if df is None or df.empty:
                continue
            out.append({"page": page_no, "md": _df_to_md(df)})
    finally:
        doc.close()
    return out


def _call_text(user_text: str, tag: str, system: str | None = None,
               schema: dict | None = None):
    """UNA llamada de texto con reintentos y ESCALADO de modelo.

    1: TABLE_MODEL+schema · 2: TABLE_MODEL sin schema · 3: GEMINI_MODEL+schema.
    (flash-preview a veces devuelve no-JSON en payloads grandes; pro remata.)
    """
    system = system or SYSTEM_TABLES
    schema = schema if schema is not None else TABLE_SCHEMA
    plans = [(config.TABLE_MODEL, schema), (config.TABLE_MODEL, None),
             (config.GEMINI_MODEL, schema)]
    for attempt, (model, sch) in enumerate(plans, 1):
        try:
            return generate_json(
                api_key=config.GEMINI_API_KEY, model=model,
                user_text=user_text, system_instruction=system,
                temperature=0.1, timeout_s=config.GEMINI_REQ_TIMEOUT,
                response_schema=sch,
            )
        except Exception as e:
            _log.warning("s4a %s intento %s (%s): %s", tag, attempt, model, e)
            if attempt < len(plans):
                time.sleep(8)
    return None


def _ids_block(ids: list[str]) -> str:
    return "\n".join("  " + ", ".join(ids[i:i + 8]) for i in range(0, len(ids), 8))


def _tables_pass(tables: list[dict], existing_ids: set[str],
                 doc_md: str = "") -> tuple[list[dict], int, dict]:
    """Extracción de TEXTO sobre las tablas gmft, en tandas centinela.

    Agrupa tablas por presupuesto de caracteres; cada grupo itera hasta tanda
    vacía / sin novedad / EXTRACT_MAX_ROUNDS.

    El req_id NO lo decide el modelo (ver utils/id_policy): solo se acepta si
    viene literal del documento. Las filas sin ID propio — el caso normal en
    tablas de parámetros — se anclan a SU tabla: `<ID del pie>.<nº de fila>`,
    o `TBL-P<pág>.<nº de fila>` si la tabla no tiene pie localizable. El nº de
    fila sale de casar el texto contra las filas de la tabla, no de un contador
    del modelo: así dos ejecuciones dan el mismo ID para la misma fila.

    Devuelve (items, n_llamadas, stats).
    """
    groups: list[list[dict]] = [[]]
    budget = 0
    for t in tables:
        if budget + len(t["md"]) > _TABLE_BATCH_CHARS and groups[-1]:
            groups.append([])
            budget = 0
        groups[-1].append(t)
        budget += len(t["md"])

    # pie de tabla con ID, resuelto UNA vez por tabla (determinista, sin API)
    captions = id_policy.caption_ids(doc_md)
    for t in tables:
        t["_owner"] = id_policy.owner_id(t["md"], doc_md, captions)
        t["_rows"] = id_policy.table_rows(t["md"])

    seen_norm = {_norm_id(i) for i in existing_ids}
    seen_ids: list[str] = sorted(existing_ids)
    seen_texts: set[str] = set()
    used_derived: set[str] = set()
    used_rows: dict[int, set[int]] = {}   # id(tabla) → filas ya asignadas
    out: list[dict] = []
    calls = 0
    stats = {"verbatim": 0, "derived_owner": 0, "derived_page": 0,
             "unanchored": 0, "sweep": 0}

    def _txt_key(s: str) -> str:
        return re.sub(r"\W+", "", s.lower())[:140]

    for gi, group in enumerate(g for g in groups if g):
        blocks = [f"--- TABLA (página {t['page']}) ---\n{t['md']}" for t in group]
        base = "\n\n".join(blocks)
        group_md = "\n".join(t["md"] for t in group)
        # Centinela por TEXTO, no por ID. Cada llamada es independiente (no hay
        # conversación), y ahora las filas sin ID propio vuelven con req_id "":
        # si solo realimentáramos IDs, la ronda 2 devolvería las mismas filas,
        # el dedup las tiraría y el bucle cortaría con la tabla a medias.
        group_returned: list[str] = []
        for rnd in range(1, config.EXTRACT_MAX_ROUNDS + 1):
            done_block = ""
            if group_returned:
                done_block = ("\n\n--- FILAS YA DEVUELTAS DE ESTAS TABLAS "
                              f"({len(group_returned)}) ---\n"
                              + "\n".join("  · " + s for s in group_returned))
            user_text = (base + f"\n\n--- REQ_IDS YA EXTRAÍDOS ({len(seen_ids)}) ---\n"
                         + _ids_block(seen_ids) + done_block
                         + "\n\nDevuelve SOLO filas que NO estén ya en las listas de"
                           " arriba. Si no queda ninguna, devuelve una lista vacía.")
            calls += 1
            data = _call_text(user_text, f"grupo {gi + 1} tanda {rnd}")
            part = data.get("requisitos") if isinstance(data, dict) else None
            part = part if isinstance(part, list) else []
            if not part:
                break
            fresh = 0
            for r in part:
                if not isinstance(r, dict):
                    continue
                rid = str(r.get("req_id") or "").strip()
                texto = str(r.get("texto") or "").strip()
                if not texto:
                    continue
                # dedup por TEXTO: con req_id vacío ya no basta el dedup por ID
                tkey = _txt_key(texto)
                if tkey in seen_texts:
                    continue

                # ¿de qué tabla y fila viene? por casado de texto, no por el
                # modelo. Cada fila se asigna UNA vez (`used_rows`): así filas
                # casi gemelas reciben índices distintos en vez de desempates.
                owner_t, row_i, best = None, None, 0.0
                for t in group:
                    i, score = id_policy.match_row(
                        texto, t["_rows"], exclude=used_rows.get(id(t), set()))
                    if i is not None and score > best:
                        owner_t, row_i, best = t, i, score

                if rid and id_policy.is_verbatim(rid, doc_md + "\n" + group_md):
                    if _norm_id(rid) in seen_norm:
                        continue
                    final_id, kind = rid, "verbatim"
                elif owner_t is not None:
                    used_rows.setdefault(id(owner_t), set()).add(row_i)
                    stem_id = owner_t["_owner"] or f"TBL-P{owner_t['page']}"
                    final_id = f"{stem_id}.{row_i}"
                    n = 1
                    while _norm_id(final_id) in seen_norm or final_id in used_derived:
                        n += 1
                        final_id = f"{stem_id}.{row_i}-{n}"
                    used_derived.add(final_id)
                    kind = "derived_owner" if owner_t["_owner"] else "derived_page"
                else:
                    # ni ID real ni fila localizable: sin ancla, no entra.
                    # Se anota igualmente como "ya devuelta" para que la ronda
                    # siguiente no vuelva a proponerla y el bucle converja.
                    stats["unanchored"] += 1
                    seen_texts.add(tkey)
                    group_returned.append(texto[:90])
                    continue

                seen_norm.add(_norm_id(final_id))
                seen_ids.append(final_id)
                seen_texts.add(tkey)
                group_returned.append(texto[:90])
                stats[kind] += 1
                page = owner_t["page"] if owner_t is not None else group[0]["page"]
                out.append({"req_id": final_id, "texto": texto,
                            "type": str(r.get("type") or "").strip().lower(),
                            "_page": page, "source": "table",
                            "_id_kind": kind})
                fresh += 1
            if fresh == 0:
                break

    # ── barrido de cobertura: filas que ninguna ronda llegó a tocar ──────────
    # El bucle libre no garantiza cobertura (el modelo decide cuándo "ya está").
    # Aquí la cobertura es determinista: sabemos qué filas de qué tabla quedan
    # sin asignar y las preguntamos una a una, con su número. El ID sigue
    # saliendo de la tabla, nunca del modelo.
    pending: list[tuple[int, dict, list[int]]] = []
    for ti, t in enumerate(tables, 1):
        free = [i for i in range(1, len(t["_rows"]) + 1)
                if i not in used_rows.get(id(t), set())]
        if free:
            pending.append((ti, t, free))

    for batch in _row_batches(pending, _TABLE_BATCH_CHARS):
        blocks = []
        index: dict[tuple[int, int], dict] = {}
        for ti, t, free in batch:
            head = t["md"].splitlines()[0] if t["md"].splitlines() else ""
            owner = t["_owner"] or f"(sin pie con ID, página {t['page']})"
            lines = [f"--- TABLA {ti} (página {t['page']} · {owner}) ---",
                     f"cabecera: {head}"]
            for i in free:
                lines.append(f"  fila {i}: {t['_rows'][i - 1]}")
                index[(ti, i)] = t
            blocks.append("\n".join(lines))
        calls += 1
        data = _call_text("\n\n".join(blocks), f"barrido de filas ({len(batch)} tablas)",
                          system=SYSTEM_ROWS, schema=ROWS_SCHEMA)
        filas = data.get("filas") if isinstance(data, dict) else None
        for r in (filas if isinstance(filas, list) else []):
            if not isinstance(r, dict):
                continue
            try:
                ti, row_i = int(r.get("tabla")), int(r.get("fila"))
            except (TypeError, ValueError):
                continue
            t = index.get((ti, row_i))
            texto = str(r.get("texto") or "").strip()
            if t is None or not texto:
                continue
            if row_i in used_rows.get(id(t), set()):
                continue
            tkey = _txt_key(texto)
            if tkey in seen_texts:
                continue
            used_rows.setdefault(id(t), set()).add(row_i)
            stem_id = t["_owner"] or f"TBL-P{t['page']}"
            final_id = f"{stem_id}.{row_i}"
            n = 1
            while _norm_id(final_id) in seen_norm or final_id in used_derived:
                n += 1
                final_id = f"{stem_id}.{row_i}-{n}"
            used_derived.add(final_id)
            seen_norm.add(_norm_id(final_id))
            seen_texts.add(tkey)
            stats["sweep"] = stats.get("sweep", 0) + 1
            stats["derived_owner" if t["_owner"] else "derived_page"] += 1
            out.append({"req_id": final_id, "texto": texto,
                        "type": str(r.get("type") or "").strip().lower(),
                        "_page": t["page"], "source": "table"})

    return out, calls, stats


def _row_batches(pending: list, budget: int):
    """Trocea (tabla, filas libres) por presupuesto de caracteres."""
    batch, size = [], 0
    for ti, t, free in pending:
        cost = sum(len(t["_rows"][i - 1]) for i in free) + 120
        if size + cost > budget and batch:
            yield batch
            batch, size = [], 0
        batch.append((ti, t, free))
        size += cost
    if batch:
        yield batch


# ── s4c′: rescate de huérfanos por TEXTO (antes del fallback caro) ───────────
# El sondeo Fase 0 mostró que el 64% de lo que v4 pagaba a precio de visión
# estaba VISIBLE en el markdown (docling lo leyó; s3 no lo taggeó).
#
# Diseño (lecciones de un doc tabla-céntrico de 500 requisitos):
# · VENTANAS de contexto local, no el md entero: flash con 438k chars devolvía
#   vacío en silencio; con extractos de ±30 líneas por ID rinde perfecto.
# · Arbitraje VERIFICABLE por ID: cada huérfano debe volver como `nuevo` o
#   como `descartado` explícito. Un silencio no es un veredicto — lo que quede
#   sin resolver tras el escalado a pro dispara el fallback. La pereza del
#   modelo es estructuralmente imposible de confundir con un descarte.

_RESCUE_BATCH_CHARS = int(os.environ.get("REQIF_RESCUE_BATCH_CHARS", "60000"))
_WIN_BEFORE, _WIN_AFTER = 5, 30  # líneas de contexto alrededor de cada ID


def _rescue_windows(lines: list[str], hits: dict[str, int]) -> list[tuple[int, int]]:
    """Ventanas [a,b) de líneas alrededor de cada hit, fusionando solapes."""
    spans = sorted((max(0, i - _WIN_BEFORE), min(len(lines), i + _WIN_AFTER + 1))
                   for i in hits.values())
    merged: list[list[int]] = []
    for a, b in spans:
        if merged and a <= merged[-1][1] + 3:
            merged[-1][1] = max(merged[-1][1], b)
        else:
            merged.append([a, b])
    return [(a, b) for a, b in merged]


def _rescue_pass(stem: str, scan_text: str, headers: list[dict],
                 orphans: list[str], known_norm: set[str],
                 ) -> tuple[list[dict], list[str], list[str], int]:
    """Arbitraje por ID sobre ventanas locales. Dos pasadas: flash → pro.

    Devuelve (items, descartados, sin_resolver, llamadas). `sin_resolver`
    incluye los IDs no localizados en el texto y los que ningún modelo
    arbitró — el caller decide el fallback con esa lista, no con silencios.
    """
    from utils.md_headers import format_for_prompt
    headers_block = format_for_prompt(headers) if headers else "(sin encabezados)"
    valid_anchors = {h.get("anchor") for h in headers}

    lines = scan_text.splitlines()
    norm_lines = [_norm_id(l) for l in lines]
    hits: dict[str, int] = {}
    for o in orphans:
        on = _norm_id(o)
        if not on:
            continue
        for i, nl in enumerate(norm_lines):
            if on in nl:
                hits[o] = i
                break
    unlocated = [o for o in orphans if o not in hits]

    out: list[dict] = []
    dismissed: list[str] = []
    seen = set(known_norm)
    calls = 0
    pending = dict(hits)

    for model in (config.TABLE_MODEL, config.GEMINI_MODEL):
        if not pending:
            break
        # empaquetar ventanas de los pendientes en lotes ≤ _RESCUE_BATCH_CHARS
        windows = _rescue_windows(lines, pending)
        batches: list[tuple[list[str], str]] = []
        cur_ids: list[str] = []
        cur_txt: list[str] = []
        cur_len = 0
        for a, b in windows:
            block = "\n".join(lines[a:b])
            ids_in = [o for o, i in pending.items() if a <= i < b]
            if cur_len + len(block) > _RESCUE_BATCH_CHARS and cur_txt:
                batches.append((cur_ids, "\n· · ·\n".join(cur_txt)))
                cur_ids, cur_txt, cur_len = [], [], 0
            cur_ids.extend(ids_in)
            cur_txt.append(block)
            cur_len += len(block)
        if cur_txt:
            batches.append((cur_ids, "\n· · ·\n".join(cur_txt)))

        next_pending: dict[str, int] = {}
        for bi, (ids, ctx) in enumerate(batches, 1):
            if not ids:
                continue
            user_text = ("--- CAPÍTULOS (anchor L número título) ---\n" + headers_block
                         + f"\n--- IDS A RESOLVER ({len(ids)}) ---\n" + _ids_block(sorted(ids))
                         + "\n--- EXTRACTOS DEL DOCUMENTO ---\n" + ctx
                         + "\n\nPronúnciate sobre CADA ID de la lista: `nuevos` o `descartados`.")
            calls += 1
            data = None
            try:
                data = generate_json(
                    api_key=config.GEMINI_API_KEY, model=model,
                    user_text=user_text, system_instruction=SYSTEM_RESCUE,
                    temperature=0.1, timeout_s=config.GEMINI_REQ_TIMEOUT,
                    response_schema=RESCUE_SCHEMA)
            except Exception as e:
                _log.warning("s4c-rescate %s lote %s (%s): %s", stem, bi, model, e)
            resolved_norm: set[str] = set()
            if isinstance(data, dict):
                for r in data.get("nuevos") or []:
                    if not isinstance(r, dict):
                        continue
                    rid = str(r.get("req_id") or "").strip()
                    texto = str(r.get("texto") or "").strip()
                    if not rid or not texto:
                        continue
                    resolved_norm.add(_norm_id(rid))
                    if _norm_id(rid) in seen:
                        continue
                    seen.add(_norm_id(rid))
                    parent = r.get("parent_header")
                    parent = parent.strip() or None if isinstance(parent, str) else None
                    tipo = str(r.get("type") or "").strip().lower()
                    out.append({"req_id": rid, "texto": texto,
                                "type": tipo if tipo in _VALID_TYPES else "requirement",
                                "parent_header": parent if parent in valid_anchors else None,
                                "source": "rescue"})
                for d in data.get("descartados") or []:
                    if isinstance(d, str) and d.strip():
                        resolved_norm.add(_norm_id(d))
                        dismissed.append(d.strip())
            for o in ids:
                if _norm_id(o) not in resolved_norm:
                    next_pending[o] = pending[o]  # sin veredicto → siguiente modelo
        pending = next_pending

    unresolved = sorted(set(pending) | set(unlocated))
    return out, sorted(set(dismissed)), unresolved, calls


# ── s4b: crops de figuras → VLM por lotes ────────────────────────────────────

def _vision_call(images: list[dict], tag: str):
    """UNA llamada de visión por lotes según backend, con reintentos."""
    for attempt in range(1, 4):
        try:
            if config.VLM_BACKEND == "local":
                from clients.vlm_local import generate_json_with_images as local_call
                return local_call(
                    model=config.VLM_LOCAL_MODEL, images=images,
                    user_text="Extrae los ítems taggeados de cada IMAGEN.",
                    system_instruction=SYSTEM_CROPS, temperature=0.1,
                    timeout_s=config.GEMINI_REQ_TIMEOUT,
                    response_schema=CROP_SCHEMA, base_url=config.VLM_LOCAL_URL,
                )
            return generate_json_with_images(
                api_key=config.GEMINI_API_KEY, model=config.CROP_MODEL,
                images=images,
                user_text="Extrae los ítems taggeados de cada IMAGEN.",
                system_instruction=SYSTEM_CROPS, temperature=0.1,
                timeout_s=config.GEMINI_REQ_TIMEOUT,
                response_schema=CROP_SCHEMA if attempt < 3 else None,
            )
        except Exception as e:
            _log.warning("s4b %s intento %s: %s", tag, attempt, e)
            if attempt < 3:
                time.sleep(8)
    return None


def _crops_pass(stem: str, regions: list[dict], headers: list[dict],
                page_anchors: list[tuple[int, str]]) -> tuple[list[dict], int]:
    """Visión sobre crops en lotes de CROP_BATCH. Devuelve (items, n_llamadas)."""
    reg_dir = config.REGIONS_DIR / stem
    anchor_title = {h.get("anchor"): (h.get("title") or "") for h in headers}
    batches = [regions[i:i + config.CROP_BATCH]
               for i in range(0, len(regions), config.CROP_BATCH)]
    out: list[dict] = []
    calls = 0
    for bi, batch in enumerate(batches, 1):
        images = []
        for k, r in enumerate(batch, 1):
            png = (reg_dir / r["png"]).read_bytes()
            anchor = _anchor_for_page(page_anchors, r["page"])
            sec = anchor_title.get(anchor, "") if anchor else ""
            label = f"IMAGEN {k} (página {r['page']}" + (f" · sección: {sec}" if sec else "") + ")"
            images.append({"label": label, "png": png})
        calls += 1
        data = _vision_call(images, f"lote {bi}/{len(batches)}")
        items = data.get("items") if isinstance(data, dict) else None
        items = items if isinstance(items, list) else []
        for it in items:
            if not isinstance(it, dict):
                continue
            rid = str(it.get("req_id") or "").strip()
            texto = str(it.get("texto") or "").strip()
            if not rid or not texto:
                continue
            try:
                k = int(it.get("crop"))
            except (TypeError, ValueError):
                k = 0
            region = batch[k - 1] if 1 <= k <= len(batch) else batch[0]
            out.append({"req_id": rid, "texto": texto,
                        "type": str(it.get("type") or "").strip().lower(),
                        "_page": region["page"], "source": "crop"})
    return out, calls


# ── proceso por documento ────────────────────────────────────────────────────

def process(stem: str) -> dict:
    ja = json.loads((config.JSON_A_DIR / f"{stem}.json").read_text(encoding="utf-8"))
    headers = ja.get("headers", [])
    valid_anchors = {h.get("anchor") for h in headers}
    existing_ids = {str(r.get("req_id")).strip() for r in ja.get("requisitos", []) if r.get("req_id")}
    existing_ids |= legacy._header_guids(headers)
    heading_labels = legacy._header_labels(headers)

    pdf = _find_pdf(stem)
    if pdf is None:
        return {"stem": stem, "error": "PDF no encontrado en inputs"}

    manifest = _load_manifest(stem)
    if manifest is None:
        # sin manifest no hay enrutado posible: ruta v4 completa (segura)
        print("    · sin manifest de regiones → fallback PDF completo")
        items = legacy.recover(stem, set(existing_ids)) if config.FALLBACK_FULLPAGE else []
        return _write_b(stem, ja, items, existing_ids,
                        {"table": 0, "crop": 0, "fallback": len(items),
                         "orphans": [], "calls_text": 0, "calls_vision": 0,
                         "note": "sin manifest"})

    page_anchors = _page_anchor_map(headers, manifest.get("page_headers", []))
    regions = manifest.get("regions", [])
    tab_regions = [r for r in regions if r["type"] == "table"]
    pic_regions = [r for r in regions if r["type"] == "picture"]

    # el markdown de docling es la FUENTE DE VERDAD para validar req_ids
    # (id_policy): se carga antes de s4a, que lo necesita para los pies de tabla.
    md_text = ""
    md_path = config.MARKDOWN_DIR / f"{stem}.md"
    if md_path.is_file():
        md_text = id_policy.unescape_md(md_path.read_text(encoding="utf-8"))

    # ── s4a: tablas → gmft → texto ──
    table_items: list[dict] = []
    tables_md_all = ""
    calls_text = 0
    id_stats: dict = {}
    if tab_regions:
        try:
            tables = _gmft_tables(pdf)
            tables_md_all = "\n".join(t["md"] for t in tables)
            if tables:
                table_items, calls_text, id_stats = _tables_pass(
                    tables, existing_ids, md_text)
            print(f"    · s4a gmft: {len(tables)} tablas → +{len(table_items)} ítems "
                  f"({calls_text} llamadas de texto)")
            if id_stats:
                print(f"      ids: {id_stats['verbatim']} literales · "
                      f"{id_stats['derived_owner']} derivados del pie de tabla · "
                      f"{id_stats['derived_page']} anclados a página · "
                      f"{id_stats['unanchored']} sin ancla (descartados) · "
                      f"{id_stats.get('sweep', 0)} del barrido de filas")
        except Exception as e:
            # gmft caído: las tablas se degradan a la ruta de visión (crops)
            _log.warning("s4a gmft no disponible (%s); tablas → visión", e)
            print(f"    · s4a gmft FALLÓ ({e}) → tablas por visión")
            pic_regions = pic_regions + tab_regions

    # ── s4b: figuras → VLM ──
    crop_items: list[dict] = []
    calls_vision = 0
    if pic_regions:
        known_after_tables = existing_ids | {i["req_id"] for i in table_items}
        raw_crop, calls_vision = _crops_pass(stem, pic_regions, headers, page_anchors)
        seen = {_norm_id(i) for i in known_after_tables}
        for it in raw_crop:
            if _norm_id(it["req_id"]) in seen:
                continue
            seen.add(_norm_id(it["req_id"]))
            crop_items.append(it)
        print(f"    · s4b crops: {len(pic_regions)} figuras en {calls_vision} lote(s) "
              f"→ +{len(crop_items)} ítems")

    # ── filtro de ruido (mismos criterios que v4) + parent determinista ──
    out: list[dict] = []
    dropped = 0
    for it in table_items + crop_items:
        texto = it["texto"]
        tipo = it["type"] if it["type"] in _VALID_TYPES else "requirement"
        if legacy._norm_label(texto) in heading_labels or (
                tipo == "information" and legacy._HEADING_SHAPE_RE.fullmatch(texto)):
            dropped += 1
            continue
        if legacy._CHANGE_HISTORY_RE.search(texto[:120]):
            dropped += 1
            continue
        anchor = _anchor_for_page(page_anchors, it.pop("_page", 0))
        out.append({"req_id": it["req_id"], "texto": texto, "type": tipo,
                    "parent_header": anchor if anchor in valid_anchors else None,
                    "source": it["source"]})

    # ── s4c: huérfanos → rescate por TEXTO → fallback ──
    known = set(existing_ids) | {i["req_id"] for i in out}
    # familias SOLO desde req_ids limpios (A + rutas); derivar de los GUIDs
    # compuestos de headers genera familias-ruido ('GUID: X / CR n')
    family_ids = {str(r.get("req_id") or "").strip()
                  for r in ja.get("requisitos", [])} | {i["req_id"] for i in out}
    family_ids = {i for i in family_ids if i}
    scan_text = md_text + "\n" + tables_md_all
    # familias regex + escáner directo de anotaciones "GUID: X" (los GUIDs hex
    # no generalizan por familia — caso real: 433 requisitos anotados)
    orphans = sorted(set(find_orphans(scan_text, known, family_ids=family_ids))
                     | set(find_annotated(scan_text, known)))

    rescue_items: list[dict] = []
    dismissed: list[str] = []
    unresolved: list[str] = list(orphans)
    calls_rescue = 0
    if orphans and scan_text.strip():
        # el 64% de lo que v4 pagaba a visión estaba EN el md: texto primero.
        # Arbitraje por ID sobre ventanas locales (flash → pro); cada huérfano
        # vuelve como ítem o como descartado explícito — un silencio no es un
        # veredicto (lección real: flash vago + confianza ciega = -453 reqs).
        rescue_items, dismissed, unresolved, calls_rescue = _rescue_pass(
            stem, scan_text, headers, orphans, {_norm_id(i) for i in known})
        out.extend(rescue_items)
        known |= {i["req_id"] for i in rescue_items}
        print(f"    · s4c rescate texto: {len(orphans)} huérfanos → "
              f"+{len(rescue_items)} ítems · {len(dismissed)} descartados · "
              f"{len(unresolved)} SIN RESOLVER ({calls_rescue} llamadas)")

    # Fallback caro si: doc clase-invisible (config), doc sin ítems por ninguna
    # ruta, o quedan IDs SIN VEREDICTO tras el escalado (la lista `unresolved`,
    # no los silencios). Los descartados explícitos NO disparan fallback.
    fallback_items: list[dict] = []
    forced_stem = any(s.lower() in stem.lower() for s in config.FORCE_FALLBACK_STEMS)
    force_fallback = forced_stem or (
        (not existing_ids or len(ja.get("requisitos", [])) == 0) and not out)
    if config.FALLBACK_FULLPAGE and (
            force_fallback or len(unresolved) > config.ORPHAN_TOLERANCE):
        reason = ("doc clase-invisible (FORCE_FALLBACK_STEMS)" if forced_stem
                  else "doc sin ítems por ninguna ruta" if force_fallback
                  else f"{len(unresolved)} IDs sin veredicto tras rescate")
        print(f"    · s4c: {reason} → fallback visión completa")
        fallback_items = legacy.recover(stem, known)
        out.extend(fallback_items)
    elif unresolved:
        print(f"    [WARNING] s4c: {len(unresolved)} IDs sin veredicto "
              f"(fallback desactivado) — lista en routes.orphans")

    # ── guarda de IDs: nada entra al contrato con un ID que no es del documento ──
    # Se aplica a TODAS las rutas (tabla, crop, rescate, fallback) por igual.
    # Aceptamos literal-del-documento o derivado-por-nosotros; lo demás fuera y
    # a la vista (un ID alucinado que imita la familia es el peor caso posible:
    # parece un OEM REQ ID real y no lo es).
    haystack = md_text + "\n" + tables_md_all
    a_shapes = id_policy.shapes_of(
        str(r.get("req_id") or "").strip() for r in ja.get("requisitos", []))
    kept, rejected = [], []
    for it in out:
        v = id_policy.verdict(it["req_id"], haystack, a_shapes)
        if v in ("reject", "alien_shape"):
            rejected.append({"req_id": it["req_id"], "source": it.get("source"),
                             "motivo": v, "texto": it["texto"][:90]})
            continue
        kept.append(it)
    if rejected:
        by_motivo = {}
        for r in rejected:
            k = (r["motivo"], r["source"])
            by_motivo[k] = by_motivo.get(k, 0) + 1
        print(f"    · guarda de IDs: {len(rejected)} ítem(s) fuera "
              f"{ {f'{m}/{s}': n for (m, s), n in by_motivo.items()} }")
        for r in rejected[:10]:
            print(f"      ✗ [{r['motivo']}] {r['req_id']}  ({r['source']})  "
                  f"{r['texto'][:55]}")
    out = kept

    return _write_b(stem, ja, out, existing_ids, {
        "table": sum(1 for i in out if i.get("source") == "table"),
        "crop": sum(1 for i in out if i.get("source") == "crop"),
        "rescue": sum(1 for i in out if i.get("source") == "rescue"),
        "fallback": sum(1 for i in out if i.get("source") == "fallback"),
        "id_policy": id_stats,
        "id_rejected": rejected,
        "orphans": unresolved,        # IDs sin veredicto (los que importan)
        "dismissed": len(dismissed),  # descartados explícitos por el árbitro
        "calls_text": calls_text,
        "calls_rescue": calls_rescue,
        "calls_vision": calls_vision,
        "dropped": dropped,
        "regions_pic": len(pic_regions),
        "regions_tab": len(tab_regions),
    })


def _write_b(stem: str, ja: dict, items: list[dict], existing_ids: set[str],
             routes: dict) -> dict:
    obj = {"stem": stem, "source_file": ja.get("source_file", ""),
           "existing": len(existing_ids), "requisitos": items, "routes": routes}
    outp = config.JSON_B_DIR / f"{stem}.json"
    outp.parent.mkdir(parents=True, exist_ok=True)
    outp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
    n_info = sum(1 for r in items if r.get("type") == "information")
    return {"stem": stem, "existing": len(existing_ids), "recovered": len(items),
            "rec_req": len(items) - n_info, "rec_info": n_info, "routes": routes}


def main() -> int:
    only = sys.argv[1] if len(sys.argv) > 1 else None
    if not config.GEMINI_API_KEY and config.VLM_BACKEND != "local":
        print("[ERR] GEMINI_API_KEY no configurada")
        return 1
    jas = sorted(config.JSON_A_DIR.glob("*.json"))
    if only:
        jas = [p for p in jas if only.lower() in p.stem.lower()]
    if not jas:
        print("No hay json_a. ¿Corriste s1–s3?")
        return 1
    from clients.gemini import usage_snapshot
    for ja in jas:
        print(f"  {ja.stem[:60]}")
        u0 = usage_snapshot()
        r = process(ja.stem)
        u1 = usage_snapshot()
        if "error" in r:
            print(f"    → ERROR: {r['error']}")
            continue
        rt = r["routes"]
        print(f"    → +{r['recovered']:3d} nuevos ({r['rec_req']} req + {r['rec_info']} info) "
              f"[tabla {rt.get('table', 0)} · crop {rt.get('crop', 0)} · "
              f"rescate {rt.get('rescue', 0)} · fallback {rt.get('fallback', 0)} · "
              f"huérfanos {len(rt.get('orphans', []))}]")
        tok = {"prompt": u1["prompt"] - u0["prompt"],
               "output": u1["output"] - u0["output"],
               "calls": u1["calls"] - u0["calls"]}
        print(f"    tokens: {tok['prompt']:,} in · {tok['output']:,} out · "
              f"{tok['calls']} llamadas")
        # persistir la contabilidad en el json_b (informe de coste)
        try:
            bp = config.JSON_B_DIR / f"{ja.stem}.json"
            obj = json.loads(bp.read_text(encoding="utf-8"))
            obj.setdefault("routes", {})["tokens"] = tok
            bp.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
