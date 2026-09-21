"""Cliente HTTP para la API Gemini (generateContent).

Único cliente en todo el proyecto. El modelo se pasa como parámetro;
los valores por defecto se leen de config.py.

No depende de ningún SDK externo — solo urllib de la stdlib.
"""

from __future__ import annotations

import json
import logging
import urllib.error
import urllib.request
from typing import Any

from utils.json_helpers import extract_json_from_llm

_log = logging.getLogger(__name__)

_GENERATE_URL = (
    "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
)


def generate_json(
    *,
    api_key: str,
    model: str,
    user_text: str,
    system_instruction: str | None = None,
    temperature: float = 0.2,
    timeout_s: float = 300.0,
    response_schema: dict | None = None,
    thinking_budget: int | None = None,
) -> Any:
    """
    Llama a :generateContent con responseMimeType application/json.

    Si se pasa `response_schema` (JSON Schema estilo OpenAPI), se impone vía
    responseSchema: la API restringe la decodificación al esquema (JSON
    garantizado bien formado, sin depender de reparaciones regex).
    `thinking_budget=0` apaga el thinking en modelos que piensan por defecto
    (los thoughts se FACTURAN y en tareas mecánicas solo aportan latencia y
    respuestas vacías intermitentes); None = no enviar el campo.

    Devuelve el objeto Python parseado (dict o list).
    Lanza urllib.error.HTTPError o ValueError en caso de fallo.
    """
    url = f"{_GENERATE_URL.format(model=model)}?key={api_key}"
    gen_cfg: dict[str, Any] = {
        "temperature": temperature,
        "responseMimeType": "application/json",
    }
    if response_schema is not None:
        gen_cfg["responseSchema"] = response_schema
    if thinking_budget is not None:
        gen_cfg["thinkingConfig"] = {"thinkingBudget": thinking_budget}
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": gen_cfg,
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    raw_response = _post(url, body, timeout_s=timeout_s)
    text = _extract_text(raw_response)
    return extract_json_from_llm(text)


def generate_json_with_images(
    *,
    api_key: str,
    model: str,
    images: list[dict],
    user_text: str,
    system_instruction: str | None = None,
    temperature: float = 0.1,
    timeout_s: float = 600.0,
    response_schema: dict | None = None,
) -> Any:
    """Variante multimodal para CROPS (v5): varias imágenes pequeñas por llamada.

    `images` = [{"label": "IMAGEN 1 (página 12, sección 5.5)", "png": bytes}...].
    Cada imagen va precedida de su etiqueta de texto para que el modelo pueda
    referirse a ella (campo `crop` del esquema). Un crop < 384px cuesta ~258
    tokens: órdenes de magnitud más barato que la página entera.
    """
    import base64

    parts: list[dict[str, Any]] = [{"text": user_text}]
    for img in images:
        parts.append({"text": img["label"]})
        parts.append({"inline_data": {
            "mime_type": "image/png",
            "data": base64.b64encode(img["png"]).decode("ascii"),
        }})

    gen_cfg: dict[str, Any] = {
        "temperature": temperature,
        "responseMimeType": "application/json",
    }
    if response_schema is not None:
        gen_cfg["responseSchema"] = response_schema
    url = f"{_GENERATE_URL.format(model=model)}?key={api_key}"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": parts}],
        "generationConfig": gen_cfg,
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    raw_response = _post(url, body, timeout_s=timeout_s)
    text = _extract_text(raw_response)
    return extract_json_from_llm(text)


def generate_json_with_pdf(
    *,
    api_key: str,
    model: str,
    pdf_path,  # type: ignore[no-untyped-def]
    user_text: str,
    system_instruction: str | None = None,
    temperature: float = 0.2,
    timeout_s: float = 600.0,
) -> Any:
    """Variante de `generate_json` que adjunta un PDF inline (multimodal real).

    El PDF se envía como `inline_data` con mime_type `application/pdf`. Gemini
    procesa cada página como ~258 tokens. Funciona hasta ~20 MB de PDF; por
    encima de eso usar File API (no implementado aquí).
    """
    import base64
    from pathlib import Path as _P

    p = _P(pdf_path)
    if not p.is_file():
        raise FileNotFoundError(f"PDF no encontrado: {p}")
    pdf_b64 = base64.b64encode(p.read_bytes()).decode("ascii")

    url = f"{_GENERATE_URL.format(model=model)}?key={api_key}"
    body: dict[str, Any] = {
        "contents": [{
            "role": "user",
            "parts": [
                {"inline_data": {"mime_type": "application/pdf", "data": pdf_b64}},
                {"text": user_text},
            ],
        }],
        "generationConfig": {
            "temperature": temperature,
            "responseMimeType": "application/json",
        },
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    raw_response = _post(url, body, timeout_s=timeout_s)
    text = _extract_text(raw_response)
    return extract_json_from_llm(text)


def generate_text(
    *,
    api_key: str,
    model: str,
    user_text: str,
    system_instruction: str | None = None,
    temperature: float = 0.2,
    timeout_s: float = 300.0,
) -> str:
    """Llamada a Gemini para respuesta en texto plano."""
    url = f"{_GENERATE_URL.format(model=model)}?key={api_key}"
    body: dict[str, Any] = {
        "contents": [{"role": "user", "parts": [{"text": user_text}]}],
        "generationConfig": {"temperature": temperature},
    }
    if system_instruction:
        body["systemInstruction"] = {"parts": [{"text": system_instruction}]}

    raw_response = _post(url, body, timeout_s=timeout_s)
    return _extract_text(raw_response)


# ---------------------------------------------------------------------------
# Contador de tokens (usageMetadata) — para el informe de coste v5
# ---------------------------------------------------------------------------
import threading as _threading

_USAGE_LOCK = _threading.Lock()
_USAGE = {"prompt": 0, "output": 0, "calls": 0}


def usage_snapshot() -> dict:
    """Copia del acumulado {prompt, output, calls} desde el arranque/reset."""
    with _USAGE_LOCK:
        return dict(_USAGE)


def usage_reset() -> None:
    with _USAGE_LOCK:
        _USAGE.update(prompt=0, output=0, calls=0)


def _track_usage(raw: dict) -> None:
    um = raw.get("usageMetadata") or {}
    with _USAGE_LOCK:
        _USAGE["prompt"] += int(um.get("promptTokenCount") or 0)
        _USAGE["output"] += int(um.get("candidatesTokenCount") or 0)
        _USAGE["calls"] += 1


# ---------------------------------------------------------------------------
# Helpers internos
# ---------------------------------------------------------------------------

def _post_raw(url: str, body: dict, *, timeout_s: float) -> dict:
    data = json.dumps(body, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
            _track_usage(raw)
            return raw
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:800] if e.fp else ""
        _log.error("Gemini HTTP %s: %s", e.code, err_body)
        raise


def _extract_text(raw: dict) -> str:
    candidates = raw.get("candidates") or []
    if not candidates:
        _log.error("Gemini sin candidates: %s", str(raw)[:500])
        raise ValueError("Respuesta Gemini vacía (sin candidates)")
    parts = (candidates[0].get("content") or {}).get("parts") or []
    text = "".join(p.get("text", "") for p in parts if isinstance(p, dict)).strip()
    if not text:
        raise ValueError("Gemini sin texto en candidates[0]")
    return text

ENGINE_NAME = "gemini"

# --- cable 2: libro de custodia. Toda llamada al modelo pasa por aquí. ---
def _post(url: str, body: dict, *, timeout_s: float) -> dict:
    import json as _json, sys as _sys
    from pathlib import Path as _Path
    from custodia.ledger import record_call, is_local
    payload = _json.dumps(body, ensure_ascii=False)
    step = _Path(_sys.argv[0]).stem if _sys.argv and _sys.argv[0] else "unknown"
    with record_call(step=step, engine=ENGINE_NAME, endpoint=url.split("/")[2] if "//" in url else url,
                     leaves_machine=not is_local(url), payload=payload) as rec:
        raw = _post_raw(url, body, timeout_s=timeout_s)
        rec["output_chars"] = len(_json.dumps(raw)) if raw is not None else 0
        return raw
