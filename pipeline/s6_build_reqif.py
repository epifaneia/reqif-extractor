"""s6 — merged.json + plantilla → .reqif final. DETERMINISTA, sin API.

Dos modos de jerarquía (para A/B en Polarion):
  · nested → v1: headers anidados por parent_anchor (= estructura del GOLD, riesgo bajo).
  · flat   → v2: todos los headers planos al tope, reqs un nivel debajo.

Reglas comunes:
  · ORDEN = `pos` (orden documental estampado por s5); fallback = orden del array.
    NO se ordena por `number` (hay docs sin numeración): el number queda solo
    como etiqueta (AD-HeadingNumber / ReqIF.ChapterNumber).
  · req_id VERBATIM · AD-HeadingLevel = level · AD-Section = "{number} {title}".
  · AD-Text compactado en una línea (idéntico al GOLD).
  · SANITIZACIÓN: todo valor emitido pasa por _esc_attr/_esc_text, que además
    de escapar elimina caracteres ilegales en XML 1.0 (un \\x00 o \\x0c en un
    texto NO puede romper el .reqif).

Uso:
  python pipeline/s6_build_reqif.py                 # construye v1_nested y v2_flat de TODOS
  python pipeline/s6_build_reqif.py AUTOSAR_SWS_COM_merged  # solo un stem
"""
from __future__ import annotations

import json
import re
import sys
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402


# ── orden / escape ───────────────────────────────────────────────────────────
def _slug(stem: str) -> str:
    return re.sub(r"[^A-Za-z0-9]+", "-", stem).strip("-").upper() + "_"


# Caracteres ilegales en XML 1.0: codepoints < 0x20 (salvo \t \n \r),
# surrogates sueltos y U+FFFE/U+FFFF. Se reemplazan por espacio antes de emitir.
_ILLEGAL_XML_RE = re.compile(
    "[\\x00-\\x08\\x0B\\x0C\\x0E-\\x1F\\uD800-\\uDFFF\\uFFFE\\uFFFF]")


def _sanitize_xml(s: str) -> str:
    return _ILLEGAL_XML_RE.sub(" ", s or "")


def _esc_attr(s: str) -> str:
    return (_sanitize_xml(s).replace("&", "&amp;").replace("<", "&lt;")
            .replace(">", "&gt;").replace('"', "&quot;"))


def _esc_text(s: str) -> str:
    return _sanitize_xml(s).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def _header_order_key(h: dict, index: int):
    """Orden documental: `pos` (estampado por s5); fallback = orden del array."""
    pos = h.get("pos")
    return (0, pos) if isinstance(pos, int) else (1, index)


def _req_order_key(r: dict, index: int):
    pos = r.get("pos")
    return (0, pos) if isinstance(pos, int) else (1, index)


# ── bloques SPEC-OBJECT ───────────────────────────────────────────────────────
def _avs(value: str, ad_ref: str) -> str:
    return (
        f'            <ATTRIBUTE-VALUE-STRING THE-VALUE="{_esc_attr(value)}">\n'
        f"              <DEFINITION>\n"
        f"                <ATTRIBUTE-DEFINITION-STRING-REF>{ad_ref}</ATTRIBUTE-DEFINITION-STRING-REF>\n"
        f"              </DEFINITION>\n"
        f"            </ATTRIBUTE-VALUE-STRING>"
    )


def _header_so(oid: str, now: str, title: str, number: str, level) -> str:
    vals = "\n".join([
        _avs(title, "AD-HeadingTitle"),
        _avs(number, "AD-HeadingNumber"),
        _avs(str(level), "AD-HeadingLevel"),
    ])
    return (
        f'        <SPEC-OBJECT IDENTIFIER="{oid}" LAST-CHANGE="{now}">\n'
        f"          <TYPE>\n"
        f"            <SPEC-OBJECT-TYPE-REF>SOT-Heading</SPEC-OBJECT-TYPE-REF>\n"
        f"          </TYPE>\n"
        f"          <VALUES>\n{vals}\n          </VALUES>\n"
        f"        </SPEC-OBJECT>"
    )


def _req_so(oid: str, now: str, req: dict, section: str, source_file: str) -> str:
    rid = req.get("req_id", "") or ""
    texto = req.get("texto", "") or ""
    # blindaje information-only: el marcador se renderiza desde `type` pegado al
    # GUID (convención del OEM), SOLO en el texto visible — AD-FullID/AD-ReqID limpios.
    rid_display = rid
    if req.get("type") == "information" and "information only" not in rid.lower():
        rid_display = f"{rid} (information only)"
    # AD-Text en UNA línea, idéntico al formato del GOLD.
    xhtml = (
        f'            <ATTRIBUTE-VALUE-XHTML><DEFINITION>'
        f"<ATTRIBUTE-DEFINITION-XHTML-REF>AD-Text</ATTRIBUTE-DEFINITION-XHTML-REF></DEFINITION>"
        f"<THE-VALUE><xhtml:div><xhtml:h3>{_esc_text(rid_display)}</xhtml:h3>"
        f"<xhtml:p>{_esc_text(texto)}</xhtml:p></xhtml:div></THE-VALUE></ATTRIBUTE-VALUE-XHTML>"
    )
    vals = "\n".join([
        _avs(rid, "AD-FullID"),
        _avs(rid, "AD-ReqID"),
        xhtml,
        _avs(section, "AD-Section"),
        _avs(source_file, "AD-Source"),
        _avs("AVAILABLE", "AD-Coverage"),
    ])
    return (
        f'        <SPEC-OBJECT IDENTIFIER="{oid}" LAST-CHANGE="{now}">\n'
        f"          <TYPE>\n"
        f"            <SPEC-OBJECT-TYPE-REF>SOT-Requirement</SPEC-OBJECT-TYPE-REF>\n"
        f"          </TYPE>\n"
        f"          <VALUES>\n{vals}\n          </VALUES>\n"
        f"        </SPEC-OBJECT>"
    )


# ── bloques SPEC-HIERARCHY (indent parametrizado) ─────────────────────────────
def _sh_leaf(sh_id: str, now: str, obj_id: str, ind: str) -> str:
    return (
        f'{ind}<SPEC-HIERARCHY IDENTIFIER="{sh_id}" LAST-CHANGE="{now}">\n'
        f"{ind}  <OBJECT>\n"
        f"{ind}    <SPEC-OBJECT-REF>{obj_id}</SPEC-OBJECT-REF>\n"
        f"{ind}  </OBJECT>\n"
        f"{ind}</SPEC-HIERARCHY>"
    )


def _sh_node(sh_id: str, now: str, obj_id: str, children_xml: str, ind: str) -> str:
    return (
        f'{ind}<SPEC-HIERARCHY IDENTIFIER="{sh_id}" LAST-CHANGE="{now}">\n'
        f"{ind}  <OBJECT>\n"
        f"{ind}    <SPEC-OBJECT-REF>{obj_id}</SPEC-OBJECT-REF>\n"
        f"{ind}  </OBJECT>\n"
        f"{ind}  <CHILDREN>\n{children_xml}\n{ind}  </CHILDREN>\n"
        f"{ind}</SPEC-HIERARCHY>"
    )


# ── núcleo ───────────────────────────────────────────────────────────────────
def build(merged_json_path: Path, output_path: Path, template_text: str,
          mode: str = "flat") -> dict:
    data = json.loads(Path(merged_json_path).read_text(encoding="utf-8"))
    headers = data.get("headers", [])
    reqs = data.get("requisitos", [])
    source_file = data.get("source_file", "") or ""

    stem = Path(merged_json_path).stem
    title = stem.upper()
    pfx = _slug(stem)
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")

    # Headers en orden documental
    headers_sorted = [h for _, h in sorted(
        enumerate(headers), key=lambda t: _header_order_key(t[1], t[0]))]

    header_id: dict[str, str] = {}
    section_label: dict[str, str] = {}
    header_so_blocks: list[str] = []
    for i, h in enumerate(headers_sorted, 1):
        oid = f"{pfx}SO-H-{i:04d}"
        a = h.get("anchor")
        header_id[a] = oid
        num = (h.get("number") or "").strip()
        ttl = (h.get("title") or "").strip()
        section_label[a] = f"{num} {ttl}".strip() if num else ttl
        header_so_blocks.append(_header_so(oid, now, ttl, num, h.get("level", "")))

    # Reqs por header (en orden) + huérfanos
    reqs_by_header: dict[str, list] = {}
    orphans: list = []
    for idx, r in enumerate(reqs):
        ph = r.get("parent_header")
        (reqs_by_header.setdefault(ph, []) if ph in header_id else orphans).append((idx, r))
    for ph in reqs_by_header:
        reqs_by_header[ph].sort(key=lambda t: _req_order_key(t[1], t[0]))

    # SPEC-OBJECT de reqs (IDs en orden documental) + oids por header
    rcount = 0
    req_so_blocks: list[str] = []
    reqoids_by_header: dict[str, list[str]] = {}
    for h in headers_sorted:
        a = h.get("anchor")
        ids: list[str] = []
        for idx, r in reqs_by_header.get(a, []):
            rcount += 1
            oid = f"{pfx}SO-R-{rcount:04d}"
            ids.append(oid)
            req_so_blocks.append(_req_so(oid, now, r, section_label[a], source_file))
        reqoids_by_header[a] = ids
    orphan_oids: list[str] = []
    for idx, r in orphans:
        rcount += 1
        oid = f"{pfx}SO-R-{rcount:04d}"
        orphan_oids.append(oid)
        req_so_blocks.append(_req_so(oid, now, r, "", source_file))

    spec_objects = "\n".join(header_so_blocks + req_so_blocks)

    # Árbol
    shc = [0]
    def next_sh() -> str:
        shc[0] += 1
        return f"{pfx}SH-{shc[0]:04d}"

    ROOT_IND = "            "  # 12 espacios (donde va {{HIERARCHY}})

    if mode == "flat":
        hier = []
        for h in headers_sorted:
            a = h.get("anchor")
            sh_id = next_sh()
            kids = reqoids_by_header.get(a, [])
            if kids:
                children = "\n".join(_sh_leaf(next_sh(), now, rid, ROOT_IND + "    ") for rid in kids)
                hier.append(_sh_node(sh_id, now, header_id[a], children, ROOT_IND))
            else:
                hier.append(_sh_leaf(sh_id, now, header_id[a], ROOT_IND))
        for rid in orphan_oids:
            hier.append(_sh_leaf(next_sh(), now, rid, ROOT_IND))
        hierarchy = "\n".join(hier)

    elif mode == "nested":
        # sub-headers por parent_anchor (en orden documental ya ordenado)
        sub_by_parent: dict[str, list] = {}
        for h in headers_sorted:
            sub_by_parent.setdefault(h.get("parent_anchor") or "", []).append(h)

        def build_subtree(parent_anchor: str, ind: str) -> str:
            out = []
            # 1) reqs directos del parent
            for rid in reqoids_by_header.get(parent_anchor, []):
                out.append(_sh_leaf(next_sh(), now, rid, ind))
            # 2) sub-headers, recursivo
            for h in sub_by_parent.get(parent_anchor, []):
                a = h.get("anchor")
                sh_id = next_sh()
                if reqoids_by_header.get(a) or sub_by_parent.get(a):
                    child = build_subtree(a, ind + "    ")
                    out.append(_sh_node(sh_id, now, header_id[a], child, ind))
                else:
                    out.append(_sh_leaf(sh_id, now, header_id[a], ind))
            return "\n".join(out)

        parts = [_sh_leaf(next_sh(), now, rid, ROOT_IND) for rid in orphan_oids]
        parts.append(build_subtree("", ROOT_IND))
        hierarchy = "\n".join(p for p in parts if p)
    else:
        raise ValueError(f"mode desconocido: {mode}")

    out = (template_text
           .replace("{{PFX}}", pfx)
           .replace("{{TITLE}}", _esc_attr(title))
           .replace("{{NOW}}", now)
           .replace("{{SPEC_OBJECTS}}", spec_objects)
           .replace("{{HIERARCHY}}", hierarchy))
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(out, encoding="utf-8")
    return {"stem": stem, "headers": len(headers), "reqs": len(reqs),
            "orphans": len(orphans)}


def main() -> int:
    template_text = config.TEMPLATE_REQIF.read_text(encoding="utf-8")
    only = sys.argv[1] if len(sys.argv) > 1 else None
    jsons = sorted(config.MERGED_JSON_DIR.glob("*.json"))
    if only:
        jsons = [p for p in jsons if p.stem == only or p.stem.startswith(only)]
    if not jsons:
        print("No hay merged.json que construir.")
        return 1

    mode = getattr(config, "BUILD_MODE", "both")
    if mode == "both":
        targets = [("v1_nested", "nested"), ("v2_flat", "flat")]
    else:
        targets = [(".", mode)]  # repo de un solo modo → salida directa

    for folder, m in targets:
        outdir = config.OUTPUT_REQIF_DIR if folder == "." else config.OUTPUT_REQIF_DIR / folder
        try:
            shown = outdir.relative_to(config.ROOT)
        except ValueError:  # outdir overrideado fuera del repo (REQIF_OUTPUT_DIR)
            shown = outdir
        print(f"== {m} → {shown} ==")
        for jp in jsons:
            r = build(jp, outdir / f"{jp.stem}.reqif", template_text, m)
            warn = f"  ⚠ {r['orphans']} huérfanos→raíz" if r["orphans"] else ""
            print(f"  {r['stem']:18s} {r['headers']:3d} h + {r['reqs']:4d} reqs{warn}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
