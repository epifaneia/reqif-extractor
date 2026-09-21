"""Configuración central del motor `reqif`: rutas y parámetros. Sin lógica."""
from __future__ import annotations

import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def _load_env(path: Path) -> None:
    """Carga KEY=VALUE de .env a os.environ (sin dependencias, no pisa lo ya puesto)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, _, v = line.partition("=")
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_env(ROOT / ".env")

# --- Datos (flujo PDF -> ... -> reqif) ---
# Overridables por variables de entorno (REQIF_*). Si NO se setean, los defaults
# son EXACTAMENTE los de siempre (cambio retrocompatible e inocuo).
def _env_dir(var: str, default: Path) -> Path:
    v = os.environ.get(var, "").strip()
    return Path(v).resolve() if v else default


_DATA_DIR        = _env_dir("REQIF_DATA_DIR", ROOT / "data")
INPUTS_DIR       = _env_dir("REQIF_INPUTS_DIR",      _DATA_DIR / "inputs")                    # PDFs fuente
MARKDOWN_DIR     = _env_dir("REQIF_MARKDOWN_DIR",    _DATA_DIR / "interim" / "markdown")      # s1 docling
REGIONS_DIR      = _env_dir("REQIF_REGIONS_DIR",     _DATA_DIR / "interim" / "regions")       # s1 crops figuras/tablas (v5)
JSON_A_DIR       = _env_dir("REQIF_JSON_A_DIR",      _DATA_DIR / "interim" / "json_a")        # pasada A (docling+LLM)
JSON_B_DIR       = _env_dir("REQIF_JSON_B_DIR",      _DATA_DIR / "interim" / "json_b")        # pasada B (visión)
MERGED_JSON_DIR  = _env_dir("REQIF_MERGED_JSON_DIR", _DATA_DIR / "interim" / "merged_json")   # s5 fusión = CONTRATO
OUTPUT_REQIF_DIR = _env_dir("REQIF_OUTPUT_DIR",      _DATA_DIR / "outputs" / "reqif")         # s6 entregable

# --- Assets ---
TEMPLATE_REQIF   = ROOT / "templates" / "polarion_template.reqif"
GOLD_REFERENCE   = ROOT / "reference" / "gold.reqif"   # opcional: un ReqIF importado con éxito en tu Polarion
PROMPTS_DIR      = ROOT / "prompts"

# --- LLM (solo s2/s3/s4; s5/s6 NO usan API) ---
GEMINI_API_KEY   = os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY", "")
GEMINI_MODEL     = os.environ.get("GEMINI_MODEL", "gemini-3.1-pro-preview")

# --- Build del reqif (s6) ---  "nested" | "flat" | "both"
BUILD_MODE       = os.environ.get("BUILD_MODE", "both")

# --- Extracción LLM (s3/s4) ---
GEMINI_REQ_MAX_CHARS = int(os.environ.get("GEMINI_REQ_MAX_CHARS", "500000"))  # límite snippet md (docs grandes)
GEMINI_REQ_TIMEOUT   = float(os.environ.get("GEMINI_REQ_TIMEOUT", "1200"))    # timeout HTTP por llamada (s); Pro tarda en docs grandes
AUDIT_EXTRAS         = os.environ.get("AUDIT_EXTRAS", "1") not in ("0", "false", "False")

# --- Visión troceada (s4) ---
# La accuracy multimodal decae con contextos grandes (~258 tokens/página); en docs
# de > CHUNK_PAGES páginas la visión se hace por rangos con solape CHUNK_OVERLAP.
CHUNK_PAGES   = int(os.environ.get("REQIF_CHUNK_PAGES", "50"))    # páginas por trozo
CHUNK_OVERLAP = int(os.environ.get("REQIF_CHUNK_OVERLAP", "3"))   # solape entre trozos

# --- Centinela de salida (s3/s4) ---
# En docs/trozos muy densos el modelo devuelve solo PARTE de los reqs en una
# llamada. Se itera en tandas (cada una excluye los req_ids ya vistos) hasta
# tanda vacía o este tope de vueltas.
EXTRACT_MAX_ROUNDS = int(os.environ.get("REQIF_EXTRACT_MAX_ROUNDS", "8"))

# --- Paralelización ---
S4_CHUNK_WORKERS = int(os.environ.get("REQIF_S4_CHUNK_WORKERS", "4"))  # trozos de visión en paralelo (I/O de red)
S1_CONCURRENCY   = int(os.environ.get("REQIF_S1_CONCURRENCY", "2"))    # docling concurrentes (pesado en CPU/RAM)
DOC_WORKERS      = int(os.environ.get("REQIF_DOC_WORKERS", "2"))       # documentos en paralelo en run.py (2: evita contencion Gemini + presion RAM docling)

# --- Enrutado por regiones (v5) ---
# s1 emite, además del markdown, crops PNG de las regiones figura/tabla que
# detecta el layout de docling (renderizadas con pypdfium2 desde el bbox; el
# markdown NO cambia respecto a v4). s4 enruta: tabla vectorial → gmft (texto,
# barato) · figura → VLM sobre el crop · resto → red de seguridad (fallback).
REGIONS_EMIT     = os.environ.get("REQIF_REGIONS_EMIT", "1") not in ("0", "false", "False")
REGIONS_SCALE    = float(os.environ.get("REQIF_REGIONS_SCALE", "2.0"))   # 2.0 ≈ 144 dpi: mínimo para fuentes pequeñas en diagramas
CROP_MIN_PX      = int(os.environ.get("REQIF_CROP_MIN_PX", "48"))        # lado mínimo del crop renderizado (px); menor = icono/línea, se descarta
CROP_PAD_PX      = int(os.environ.get("REQIF_CROP_PAD_PX", "8"))         # padding de contexto alrededor del bbox
CROP_BATCH       = int(os.environ.get("REQIF_CROP_BATCH", "8"))          # crops por llamada de visión

# --- Backend VLM para crops (s4b) ---  "gemini" | "local"
# gemini: API cloud (vía segura ya aprobada). local: servidor vLLM/SGLang con
# API compatible OpenAI (GLM-OCR / olmOCR-2...) — mismo contrato, drop-in.
VLM_BACKEND      = os.environ.get("REQIF_VLM_BACKEND", "gemini")
CROP_MODEL       = os.environ.get("REQIF_CROP_MODEL", "") or GEMINI_MODEL   # modelo visión para crops (p.ej. flash: más barato)
TABLE_MODEL      = os.environ.get("REQIF_TABLE_MODEL", "") or GEMINI_MODEL  # modelo TEXTO para tablas gmft
VLM_LOCAL_URL    = os.environ.get("REQIF_VLM_LOCAL_URL", "http://localhost:8000/v1")
VLM_LOCAL_MODEL  = os.environ.get("REQIF_VLM_LOCAL_MODEL", "zai-org/GLM-OCR")

# --- Red de seguridad (s4c) ---
# Tras las rutas baratas se buscan IDs "huérfanos" (matchean la forma de los IDs
# ya extraídos pero no fueron capturados). Si quedan, el doc cae al fallback:
# visión de PDF completo (el s4 de v4, preservado). Garantiza el KPI de recall.
FALLBACK_FULLPAGE = os.environ.get("REQIF_FALLBACK_FULLPAGE", "1") not in ("0", "false", "False")
ORPHAN_TOLERANCE  = int(os.environ.get("REQIF_ORPHAN_TOLERANCE", "0"))   # nº de huérfanos tolerados sin disparar fallback
# Docs "clase invisible": anotan IDs con fuente sin ToUnicode — se VEN en el
# render pero NINGUNA extracción de texto los devuelve (ni docling ni pdfium).
# Solo la visión de página completa los captura → fallback forzado por nombre
# (substrings separados por coma). No hay detector determinista fiable (se
# intentó por content-stream: no discrimina); pendiente sonda visual barata.
FORCE_FALLBACK_STEMS = [s.strip() for s in
                        os.environ.get("REQIF_FORCE_FALLBACK_STEMS", "").split(",")
                        if s.strip()]
