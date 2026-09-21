"""Saneamiento determinista de los .reqif (anti-errores de import en Polarion).

Opera SOLO sobre los valores (ATTRIBUTE-VALUE-STRING / -XHTML); NUNCA toca
SPEC-TYPES, DATATYPES, LONG-NAMEs mágicos, IDENTIFIERs, refs ni el TITLE.

Qué limpia de cada texto:
  1. Control / no imprimibles XML-ilegales: \\x00-\\x08 \\x0B \\x0C \\x0E-\\x1F \\x7F.
  2. Invisibles Unicode: zero-width, bidi, word-joiner, BOM suelto, soft-hyphen.
  3. Espacios raros (NBSP, figure/narrow nbsp, separadores de línea/párrafo) -> espacio.
  4. Etiquetas HTML de formato sueltas (<b> <i> <br> <span> <p> <li> ...), abiertas,
     cerradas o sin cerrar -> se eliminan. Los <placeholder> de dominio (<n>, <key_id>)
     se RESPETAN (solo se quitan tags HTML reales, no letras sueltas tipo <n>).
  5. Escapes dobles (&lt;b&gt;, &amp;lt;) -> se decodifican y la regla 4 los limpia.
  6. Whitespace: colapsa espacios, recorta, máx. 2 saltos seguidos.
  7. Campos STRING vacíos -> se elimina el ATTRIBUTE-VALUE-STRING (anti-null), salvo
     AD-ReqID/AD-FullID/AD-HeadingTitle (esos: WARNING, no se borran).
  8. <xhtml:div> sin texto -> se le inserta un <xhtml:p/> mínimo (anti rich-text nulo).

Genera copias en data/outputs/reqif/<mode>_clean/ (NO toca los de entrada).

Uso:  python tools/sanitize_reqif.py            # sobre *_shortid (recomendado)
      python tools/sanitize_reqif.py v1_nested  # sobre las carpetas que se pasen
"""
from __future__ import annotations

import re
import sys
from collections import Counter
from pathlib import Path
from lxml import etree

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import config  # noqa: E402

REQIF = "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd"
XHTML = "http://www.w3.org/1999/xhtml"
def Q(t: str) -> str: return f"{{{REQIF}}}{t}"
def X(t: str) -> str: return f"{{{XHTML}}}{t}"

# 1) Control chars XML-ilegales (conserva \t \n \r).
RE_CTRL = re.compile(r"[\x00-\x08\x0B\x0C\x0E-\x1F\x7F]")
# 2) Invisibles Unicode (zero-width, bidi, word-joiner, BOM, soft-hyphen, noncharacters).
RE_INVIS = re.compile("[­​-‏‪-‮⁠-⁤﻿￹-￻￾￿]")
# 3) Espacios "raros" -> espacio normal.
RE_NBSP = re.compile("[   ]")
RE_LINESEP = re.compile("[  ]")
# 4) Etiquetas HTML de formato (whitelist). Abiertas/cerradas/auto-cerradas, con o sin atributos.
# OJO: solo tags HTML reales. NO incluir letras sueltas tipo 'n' que en specs son
# placeholders de dominio (`KEY_<n>`, `IME Feature ID <n>`) — borrarlas pierde contenido.
_HTML_TAGS = (r"b|i|u|s|br|em|strong|span|font|sub|sup|tt|code|small|big|strike|del|ins|"
              r"mark|p|div|ul|ol|li|dl|dt|dd|h[1-6]|table|thead|tbody|tfoot|tr|td|th|pre|"
              r"blockquote|a|img|hr|center|label|abbr")
RE_HTMLTAG = re.compile(rf"</?(?:{_HTML_TAGS})(?:\s[^>]*)?/?>", re.IGNORECASE)
# 6) Whitespace.
RE_SPACES = re.compile(r"[ \t]+")
RE_NL3 = re.compile(r"\n{3,}")
RE_TRAIL = re.compile(r"[ \t]+\n")

# Campos cuyo vacío NO se borra en silencio (clave de matching / nombre de heading).
KEEP_IF_EMPTY = {"AD-ReqID", "AD-FullID", "AD-HeadingTitle"}


def _decode_escapes(s: str) -> str:
    """Revela etiquetas escondidas por escape simple o doble; respeta &amp; legítimo."""
    return (s.replace("&amp;lt;", "<").replace("&amp;gt;", ">")
             .replace("&lt;", "<").replace("&gt;", ">"))


def _common(s: str, st: Counter) -> str:
    s = _decode_escapes(s)
    st["ctrl"] += len(RE_CTRL.findall(s))
    s = RE_CTRL.sub("", s)
    st["invis"] += len(RE_INVIS.findall(s))
    s = RE_INVIS.sub("", s)
    st["nbsp"] += len(RE_NBSP.findall(s))
    s = RE_NBSP.sub(" ", s)
    s = RE_LINESEP.sub("\n", s)
    st["tags"] += len(RE_HTMLTAG.findall(s))
    s = RE_HTMLTAG.sub("", s)
    return s


def clean_inline(s: str, st: Counter) -> str:
    """STRING (una línea): colapsa TODO el whitespace a espacios."""
    if not s:
        return s
    s = _common(s, st)
    return RE_SPACES.sub(" ", s.replace("\n", " ").replace("\r", " ")).strip()


def clean_rich(s: str, st: Counter) -> str:
    """XHTML (multilínea): conserva saltos, recorta finales de línea, máx. 2 saltos."""
    if not s:
        return s
    s = _common(s, st)
    s = RE_SPACES.sub(" ", s)
    s = RE_TRAIL.sub("\n", s)
    s = RE_NL3.sub("\n\n", s)
    return s.strip()


def _ad_of(value_el) -> str | None:
    """Devuelve el IDENTIFIER de la ATTRIBUTE-DEFINITION-*-REF de un ATTRIBUTE-VALUE-*."""
    for ref in value_el.iter():
        if ref.tag in (Q("ATTRIBUTE-DEFINITION-STRING-REF"), Q("ATTRIBUTE-DEFINITION-XHTML-REF")):
            return (ref.text or "").strip()
    return None


def sanitize_tree(root, st: Counter) -> None:
    # --- STRING values ---
    for val in list(root.iter(Q("ATTRIBUTE-VALUE-STRING"))):
        raw = val.get("THE-VALUE", "")
        clean = clean_inline(raw, st)
        if clean != raw:
            val.set("THE-VALUE", clean)
        if clean == "":
            ad = _ad_of(val)
            if ad in KEEP_IF_EMPTY:
                st[f"empty_keep:{ad}"] += 1
            else:
                val.getparent().remove(val)
                st["empty_removed"] += 1

    # --- XHTML values ---
    for val in root.iter(Q("ATTRIBUTE-VALUE-XHTML")):
        the = val.find(Q("THE-VALUE"))
        if the is None:
            continue
        has_text = False
        for node in the.iter():
            if node.text:
                c = clean_rich(node.text, st)
                if c != node.text:
                    node.text = c
                has_text = has_text or bool(c.strip())
            if node.tail:
                node.tail = clean_rich(node.tail, st)
        if not has_text:
            div = the.find(X("div"))
            if div is not None and div.find(X("p")) is None:
                etree.SubElement(div, X("p"))
                st["xhtml_empty_fixed"] += 1


def process(src_dir: Path, dst_dir: Path) -> Counter:
    files = sorted(src_dir.glob("*.reqif"))
    total = Counter()
    if not files:
        print(f"  (sin reqif en {src_dir})")
        return total
    dst_dir.mkdir(parents=True, exist_ok=True)
    for f in files:
        st: Counter = Counter()
        tree = etree.parse(str(f))
        sanitize_tree(tree.getroot(), st)
        tree.write(str(dst_dir / f.name), xml_declaration=True, encoding="utf-8")
        total.update(st)
        bits = [f"ctrl={st['ctrl']}", f"invis={st['invis']}", f"nbsp={st['nbsp']}",
                f"tags={st['tags']}", f"vacios_borrados={st['empty_removed']}"]
        warns = [k for k in st if k.startswith("empty_keep:") or k == "xhtml_empty_fixed"]
        w = ("  WARN " + " ".join(f"{k}={st[k]}" for k in warns)) if warns else ""
        print(f"  {f.name[:40]:40s} " + " ".join(bits) + w)
    return total


def main() -> int:
    modes = sys.argv[1:] or ["v1_nested_shortid", "v2_flat_shortid"]
    base = config.OUTPUT_REQIF_DIR
    grand = Counter()
    for mode in modes:
        src = base / mode
        dst = base / (mode + "_clean")
        print(f"== {mode} -> {dst.name} ==")
        grand.update(process(src, dst))
    print("\n== total saneado ==")
    for k in sorted(grand):
        print(f"  {k}: {grand[k]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
