"""Pruebas de las piezas deterministas de salida. Sin red, sin API, datos sintéticos.

    python tests/test_salida.py

  · s3: un `type` inválido no se corrige en silencio (`type_coerced`).
  · s6: merged.json sintético → .reqif bien formado, id verbatim en
    ReqIF.ForeignID (AD-ReqID) y el marcador "(information only)" solo en el
    texto visible.
  · tools/crop_ids: prefijo recortado al código del documento, referencias
    reescritas, códigos repetidos desambiguados.
  · tools/sanitize_reqif: limpia los valores sin tocar los identificadores.

s6/crop_ids/sanitize_reqif necesitan lxml (requirements-core.txt); sin él esas
pruebas se saltan y se dice.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))

os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["REQIF_DATA_DIR"] = tempfile.mkdtemp()


def _sin_red(*a, **kw):
    raise AssertionError("la prueba intentó salir a la red")


urllib.request.urlopen = _sin_red

import config  # noqa: E402
import s3_requirements  # noqa: E402
import s6_build_reqif  # noqa: E402

fallos = 0


def check(cond: bool, msg: str) -> None:
    global fallos
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        fallos += 1


NS = {"r": "http://www.omg.org/spec/ReqIF/20110401/reqif.xsd",
      "x": "http://www.w3.org/1999/xhtml"}


def _merged(tmp: Path, stem: str) -> Path:
    obj = {
        "stem": stem, "source_file": f"{stem}.pdf",
        "headers": [
            {"anchor": "H0001", "level": 1, "number": "1", "title": "Synthetic scope", "parent_anchor": ""},
            {"anchor": "H0002", "level": 2, "number": "1.1", "title": "Synthetic limits", "parent_anchor": "H0001"},
        ],
        "requisitos": [
            {"req_id": "SYN_Mod_00100", "texto": "The module shall start. <b>Fast</b>.",
             "type": "requirement", "parent_header": "H0002"},
            {"req_id": "SYN_Mod_00101", "texto": "Background note on start-up.",
             "type": "information", "parent_header": "H0001"},
        ],
    }
    p = tmp / "merged" / f"{stem}.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(obj), encoding="utf-8")
    return p


def _values(root, ad: str) -> list[str]:
    return [v.get("THE-VALUE") for v in root.iter(f"{{{NS['r']}}}ATTRIBUTE-VALUE-STRING")
            if v.findtext(".//r:ATTRIBUTE-DEFINITION-STRING-REF", namespaces=NS) == ad]


def test_s3_type_coerced() -> None:
    raw = [{"req_id": "SYN_1", "texto": "a", "type": "requirement", "parent_header": "H0001"},
           {"req_id": "SYN_2", "texto": "b", "type": "Information", "parent_header": "HX999"},
           {"req_id": "SYN_3", "texto": "c", "type": "note"},
           {"req_id": "SYN_4", "texto": "d"}]
    out, coerced = s3_requirements._finalize_reqs(raw, {"H0001"})
    check(len(out) == 4, "s3: ningún ítem se pierde por un `type` inválido")
    check([o["type"] for o in out] == ["requirement", "information", "requirement", "requirement"],
          "s3: inválidos → requirement; 'Information' se normaliza sin marcar")
    check([o.get("type_coerced", False) for o in out] == [False, False, True, True],
          "s3: el ítem forzado lleva type_coerced")
    check(coerced == [{"req_id": "SYN_3", "type_raw": "note"}, {"req_id": "SYN_4", "type_raw": ""}],
          "s3: las coerciones se listan con su valor original")
    check(out[1]["parent_header"] is None, "s3: parent_header desconocido → None")
    req_props = s3_requirements.REQS_SCHEMA["properties"]["requisitos"]["items"]["properties"]
    check(req_props["type"]["enum"] == ["requirement", "information"],
          "s3: el esquema de la pasada A restringe `type` al enum")

    # _llm_pass con el modelo sustituido: cada llamada lleva el esquema y la
    # coerción llega hasta la salida del paso.
    llamadas = []

    def fake_json(**kw):
        llamadas.append(kw)
        if len(llamadas) > 1:
            return {"headers_adicionales": [], "requisitos": []}
        return {"headers_adicionales": [],
                "requisitos": [{"req_id": "SYN_9", "texto": "x shall y", "type": "nota"}]}

    original = s3_requirements.gemini_json
    s3_requirements.gemini_json = fake_json
    try:
        hdrs = [{"anchor": "H0001", "level": 1, "number": "1", "title": "T", "raw": "1 T",
                 "parent_anchor": ""}]
        _h, reqs, rounds, coerced = s3_requirements._llm_pass("SYN", "# 1 T\nx shall y [SYN_9]", hdrs)
    finally:
        s3_requirements.gemini_json = original
    check(llamadas and all(k.get("response_schema") is s3_requirements.REQS_SCHEMA for k in llamadas),
          f"s3: las {len(llamadas)} llamadas de extracción llevan REQS_SCHEMA")
    check(rounds == 2 and len(reqs) == 1 and reqs[0].get("type_coerced") is True
          and coerced == [{"req_id": "SYN_9", "type_raw": "nota"}],
          "s3: _llm_pass conserva el ítem y devuelve la coerción")


def test_salida_reqif(tmp: Path) -> None:
    try:
        from lxml import etree
    except ImportError:
        print("  skip s6/crop_ids/sanitize_reqif: falta lxml (pip install -r requirements-core.txt)")
        return
    sys.path.insert(0, str(ROOT / "tools"))
    import crop_ids
    import sanitize_reqif

    template = config.TEMPLATE_REQIF.read_text(encoding="utf-8")
    base = tmp / "reqif" / "v1_nested"
    for stem in ("SYN_DEMO_A", "SYN_DEMO_B"):
        r = s6_build_reqif.build(_merged(tmp, stem), base / f"{stem}.reqif", template, "nested")
        check(r["reqs"] == 2 and r["orphans"] == 0, f"s6: {stem} con 2 ítems y 0 huérfanos")

    root = etree.parse(str(base / "SYN_DEMO_A.reqif")).getroot()
    check(_values(root, "AD-ReqID") == ["SYN_Mod_00101", "SYN_Mod_00100"],
          "s6: ReqIF.ForeignID (AD-ReqID) lleva el id verbatim, en orden documental")
    check(not any("information only" in v for v in _values(root, "AD-ReqID") + _values(root, "AD-FullID")),
          "s6: AD-ReqID / AD-FullID sin marcador")
    h3 = [e.text for e in root.iter(f"{{{NS['x']}}}h3")]
    check("SYN_Mod_00101 (information only)" in h3, "s6: el marcador va solo en el texto visible")
    check(all(len(the) == 1 for the in root.iter(f"{{{NS['r']}}}THE-VALUE")),
          "s6: un solo <xhtml:div> raíz por valor")
    so_ids = {e.get("IDENTIFIER") for e in root.iter(f"{{{NS['r']}}}SPEC-OBJECT")}
    refs = {e.text for e in root.iter(f"{{{NS['r']}}}SPEC-OBJECT-REF")}
    check(refs and refs <= so_ids, "s6: todas las referencias resuelven")

    # crop_ids: v1_nested → v1_nested_shortid
    short = tmp / "reqif" / "v1_nested_shortid"
    crop_ids.process(base, short)
    ids = {}
    for stem in ("SYN_DEMO_A", "SYN_DEMO_B"):
        r2 = etree.parse(str(short / f"{stem}.reqif")).getroot()
        so = {e.get("IDENTIFIER") for e in r2.iter(f"{{{NS['r']}}}SPEC-OBJECT")}
        rf = {e.text for e in r2.iter(f"{{{NS['r']}}}SPEC-OBJECT-REF")}
        ids[stem] = so
        check(rf <= so, f"crop_ids: {stem} referencias reescritas y resueltas")
        check(_values(r2, "AD-ReqID") == ["SYN_Mod_00101", "SYN_Mod_00100"],
              f"crop_ids: {stem} no toca los ids de requisito")
    check(all(i.startswith("SYN_") for i in ids["SYN_DEMO_A"])
          and all(i.startswith("SYN-2_") for i in ids["SYN_DEMO_B"]),
          "crop_ids: mismo código en dos documentos → SYN_ y SYN-2_")
    check(not (ids["SYN_DEMO_A"] & ids["SYN_DEMO_B"]), "crop_ids: sin ids de instancia repetidos entre documentos")

    # sanitize_reqif: v1_nested_shortid → v1_nested_shortid_clean
    clean = tmp / "reqif" / "v1_nested_shortid_clean"
    st = sanitize_reqif.process(short, clean)
    r3 = etree.parse(str(clean / "SYN_DEMO_A.reqif")).getroot()
    ps = [e.text or "" for e in r3.iter(f"{{{NS['x']}}}p")]
    check("The module shall start. Fast." in ps, "sanitize: NBSP → espacio y <b> fuera del texto")
    check(st["tags"] >= 2 and st["nbsp"] >= 1, f"sanitize: lo limpiado se cuenta (tags={st['tags']}, nbsp={st['nbsp']})")
    check(_values(r3, "AD-ReqID") == ["SYN_Mod_00101", "SYN_Mod_00100"],
          "sanitize: identificadores intactos")


def main() -> int:
    tmp = Path(tempfile.mkdtemp())
    test_s3_type_coerced()
    test_salida_reqif(tmp)
    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} fallos'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
