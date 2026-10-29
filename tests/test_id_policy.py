"""Pruebas de la política de identificadores (utils/id_policy). Sin red, sin API.

    python tests/test_id_policy.py

Todo el texto es SINTÉTICO (familia inventada `SYN_Mod_…`): ningún contenido
de especificación real. Se prueba:
  · las cuatro salidas de la política: verbatim, derivado del pie de tabla
    (`SYN_Mod_00110.3`), anclado a página (`TBL-P31.2`) y rechazo reportado;
  · la reproducción del caso medido (el modelo inventa casi todos los ids de
    la ruta de tablas imitando la convención del documento): con la ruta de
    tablas de s4 y la guarda final, ningún id inventado llega a la salida;
  · que el id de una fila no depende del orden en que el modelo la devuelve.

La llamada al modelo se sustituye por una respuesta fija; urlopen queda
bloqueado para que cualquier intento de red falle la prueba.
"""
from __future__ import annotations

import os
import sys
import tempfile
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "pipeline"))

# Antes de importar config: sin clave (setdefault no pisa lo ya puesto) y con
# los datos en un directorio temporal. Ninguna prueba debe tocar la red.
os.environ["GEMINI_API_KEY"] = ""
os.environ["GOOGLE_API_KEY"] = ""
os.environ["REQIF_DATA_DIR"] = tempfile.mkdtemp()


def _sin_red(*a, **kw):
    raise AssertionError("la prueba intentó salir a la red")


urllib.request.urlopen = _sin_red

from utils import id_policy  # noqa: E402
import s4_vision  # noqa: E402 — solo stdlib al importar; gmft/docling son perezosos

fallos = 0


def check(cond: bool, msg: str) -> None:
    global fallos
    print(("  ok  " if cond else "  FAIL") + " " + msg)
    if not cond:
        fallos += 1


# ── documento sintético ──────────────────────────────────────────────────────
REAL_IDS = [f"SYN_Mod_{n:05d}" for n in range(100, 120)]   # 20 ids reales

TABLE_OWNED = """\
| Parameter | Min | Max | Unit |
|---|---|---|---|
| Supply voltage nominal range | 9 | 16 | V |
| Standby current maximum draw | 0 | 50 | uA |
| Wakeup pulse minimum width | 5 | 20 | us |
| Ambient temperature operating band | -40 | 85 | C |"""

TABLE_ORPHAN = """\
| Signal | Value |
|---|---|
| Heartbeat interval period length | 100 ms |
| Watchdog timeout window length | 250 ms |
| Bootloader handshake retry count | 3 times |"""


def _doc_md() -> str:
    lines = ["## 5 Synthetic module", ""]
    for i, rid in enumerate(REAL_IDS[:10]):
        lines.append(f"The synthetic module shall perform check number {i} on start. [{rid}]")
    lines += ["", "Table 3: Synthetic electrical limits [SYN_Mod_00110]", "", TABLE_OWNED, ""]
    # Relleno > lookback (30 líneas) para que la segunda tabla no herede el pie
    # de la primera: su id debe salir anclado a página.
    for i, rid in enumerate(REAL_IDS[11:]):
        lines.append(f"The synthetic module shall report status word {i} on request. [{rid}]")
    lines += [f"Filler paragraph {i} with descriptive text only." for i in range(30)]
    lines += ["", "Table 4: Synthetic timing values", "", TABLE_ORPHAN, ""]
    lines.append("See ISO 11898-6 for the physical layer.")
    return "\n".join(lines)


DOC_MD = _doc_md()
TABLES = [{"page": 12, "md": TABLE_OWNED}, {"page": 31, "md": TABLE_ORPHAN}]

# Lo que devolvió el modelo en la ruta de tablas: ids que IMITAN la convención
# del documento (numeración de filas), ninguno escrito en la fila.
INVENTED = [
    ("SYN_Mod_00001", "Supply voltage nominal range shall be between 9 V and 16 V."),
    ("SYN_Mod_00002", "Standby current maximum draw shall be between 0 uA and 50 uA."),
    ("SYN_Mod_00003", "Wakeup pulse minimum width shall be between 5 us and 20 us."),
    ("SYN_Mod_00004", "Ambient temperature operating band shall be between -40 C and 85 C."),
    ("SYN_Mod_00005", "Heartbeat interval period length shall be 100 ms."),
    ("SYN_Mod_00006", "Watchdog timeout window length shall be 250 ms."),
    ("SYN_Mod_00007", "Bootloader handshake retry count shall be 3 times."),
    ("SYN_Mod_00110.9", "Coolant flow sensor calibration drift shall stay below two percent."),
]
# Un ítem con id real que la pasada A no tenía: debe entrar tal cual.
VERBATIM_NEW = ("SYN_Mod_00110", "Synthetic electrical limits table caption with its own id.")


def _fake_call_text(orden: list[tuple[str, str]]):
    """Sustituto de s4_vision._call_text: una tanda con `orden`, luego vacío."""
    estado = {"n": 0}

    def fake(user_text: str, tag: str, system=None, schema=None):
        if system is s4_vision.SYSTEM_ROWS:
            return {"filas": []}           # barrido de cobertura: nada nuevo
        estado["n"] += 1
        if estado["n"] > 1:
            return {"requisitos": []}
        return {"requisitos": [{"req_id": r, "texto": t, "type": "requirement"}
                               for r, t in orden]}
    return fake


def _run_tables(orden: list[tuple[str, str]]) -> tuple[list[dict], dict]:
    original = s4_vision._call_text
    s4_vision._call_text = _fake_call_text(orden)
    try:
        tables = [dict(t) for t in TABLES]
        existing = set(REAL_IDS[:10])     # lo que ya extrajo la pasada A
        out, _calls, stats = s4_vision._tables_pass(tables, existing, DOC_MD)
    finally:
        s4_vision._call_text = original
    return out, stats


def main() -> int:
    hay = DOC_MD
    shapes = id_policy.shapes_of(REAL_IDS)

    # --- 1 · verbatim ---
    check(id_policy.verdict("SYN_Mod_00105", hay, shapes) == "verbatim",
          "id literal del documento → verbatim")
    check(id_policy.verdict("SYN\\_Mod\\_00105", hay, shapes) == "reject",
          "id con escapes de markdown sin deshacer → no es literal")
    check(id_policy.unescape_md("SYN\\_Mod\\_00105") == "SYN_Mod_00105",
          "unescape_md deshace los escapes de docling")

    # --- 2 · derivado del pie de tabla ---
    caps = id_policy.caption_ids(DOC_MD)
    check([c for _, c in caps] == ["SYN_Mod_00110"],
          "solo los pies 'Table N: … [ID]' cuentan como dueños de tabla")
    check(id_policy.owner_id(TABLE_OWNED, DOC_MD, caps) == "SYN_Mod_00110",
          "la tabla con pie encuentra su id")
    check(id_policy.owner_id(TABLE_ORPHAN, DOC_MD, caps) is None,
          "la tabla sin pie no hereda el de otra tabla lejana")
    rows = id_policy.table_rows(TABLE_OWNED)
    check(len(rows) == 4, "table_rows: 4 filas de datos, sin cabecera ni separador")
    i, _ = id_policy.match_row("Wakeup pulse minimum width shall be 5 to 20 us", rows)
    check(i == 3, "match_row casa el texto con su fila (3)")
    i2, _ = id_policy.match_row("Wakeup pulse minimum width shall be 5 to 20 us", rows, exclude={3})
    check(i2 != 3, "match_row no reasigna una fila ya usada")
    check(id_policy.verdict("SYN_Mod_00110.3", hay, shapes) == "derived",
          "'<id del pie>.<fila>' → derivado")
    check(id_policy.verdict("SYN_Mod_00110.3-2", hay, shapes) == "derived",
          "el desempate '-n' sigue siendo derivado")

    # --- 3 · anclado a página ---
    check(id_policy.verdict("TBL-P31.2", hay, shapes) == "derived", "TBL-P31.2 → derivado")
    check("TBL-P31.2" not in hay and id_policy.id_shape("TBL-P31.2") not in shapes,
          "la forma anclada a página no es literal ni de la familia del documento")

    # --- 4 · rechazo ---
    check(id_policy.verdict("SYN_Mod_00007", hay, shapes) == "reject",
          "id con la forma perfecta que NO está en el documento → reject")
    check(id_policy.verdict("", hay, shapes) == "reject", "id vacío → reject")
    check(id_policy.verdict("ISO 11898-6", hay, shapes) == "alien_shape",
          "referencia normativa literal pero de forma ajena → alien_shape")

    # --- 5 · reproducción: el modelo inventa los ids de la ruta de tablas ---
    orden = INVENTED + [VERBATIM_NEW]
    out, stats = _run_tables(orden)
    ids_out = {it["req_id"] for it in out}
    inventados = {r for r, _ in INVENTED}
    check(not (ids_out & inventados),
          f"0 de {len(inventados)} ids inventados llegan a la salida de la ruta de tablas")
    check("SYN_Mod_00110" in ids_out, "el id real que trae una fila entra verbatim")
    por_texto = {it["texto"]: it["req_id"] for it in out}
    check(por_texto.get(INVENTED[2][1]) == "SYN_Mod_00110.3",
          "fila 3 de la tabla con pie → SYN_Mod_00110.3")
    check(por_texto.get(INVENTED[5][1]) == "TBL-P31.2",
          "fila 2 de la tabla sin pie (pág. 31) → TBL-P31.2")
    check(INVENTED[7][1] not in por_texto and stats["unanchored"] == 1,
          "texto que no casa con ninguna fila → fuera (unanchored), no inventa id")
    check(stats["derived_owner"] == 4 and stats["derived_page"] == 3,
          f"4 derivados del pie + 3 anclados a página (stats={stats})")

    # guarda final de s4 (misma llamada que s4_vision.process) sobre todo lo que
    # sale de la ruta de tablas MÁS una ruta de crops con ids inventados.
    crops = [{"req_id": "SYN_Mod_00008", "texto": "invented on a figure"},
             {"req_id": "SYN_Mod_00119", "texto": "real id read from a figure"},
             {"req_id": "ISO 11898-6", "texto": "a standard reference"}]
    todos = out + crops
    kept = [it for it in todos
            if id_policy.verdict(it["req_id"], hay, shapes) not in ("reject", "alien_shape")]
    rechazados = [it["req_id"] for it in todos if it not in kept]
    check(not ({it["req_id"] for it in kept} & (inventados | {"SYN_Mod_00008"})),
          "la guarda final no deja pasar ningún id inventado")
    check(sorted(rechazados) == ["ISO 11898-6", "SYN_Mod_00008"],
          f"rechazados y reportados: {sorted(rechazados)}")
    check(len(kept) == len(out) + 1, "lo derivado y lo verbatim sobrevive a la guarda")

    # --- 6 · reproducible: el orden de la respuesta no cambia los ids ---
    out_inv, _ = _run_tables(list(reversed(orden)))
    check({it["texto"]: it["req_id"] for it in out_inv} == por_texto,
          "mismo texto → mismo id aunque el modelo devuelva las filas en otro orden")

    print(f"\n{'TODO OK' if fallos == 0 else f'{fallos} fallos'}")
    return 1 if fallos else 0


if __name__ == "__main__":
    raise SystemExit(main())
