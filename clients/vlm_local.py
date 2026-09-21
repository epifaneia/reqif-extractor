"""Cliente VLM LOCAL (vLLM/SGLang, API compatible OpenAI) — backend alternativo v5.

Mismo contrato que clients.gemini.generate_json_with_images: el router s4b
elige backend por config.VLM_BACKEND ("gemini" | "local") sin tocar nada más.
Pensado para servir GLM-OCR (0.9B) u olmOCR-2 (7B) con decodificación guiada:
el esquema JSON viaja en `guided_json` (XGrammar en vLLM ≥0.8; pinear
xgrammar==0.1.16) → JSON estructuralmente garantizado, cero regex de reparación.

Servidor esperado (ejemplo):
  vllm serve zai-org/GLM-OCR --port 8000
No depende de ningún SDK — solo urllib de la stdlib, como clients.gemini.
"""
from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from typing import Any

from utils.json_helpers import extract_json_from_llm

_log = logging.getLogger(__name__)


def _generate_json_with_images_raw(
    *,
    api_key: str = "",            # ignorado (paridad de firma con gemini)
    model: str,
    images: list[dict],
    user_text: str,
    system_instruction: str | None = None,
    temperature: float = 0.1,
    timeout_s: float = 600.0,
    response_schema: dict | None = None,
    base_url: str = "http://localhost:8000/v1",
) -> Any:
    """POST /chat/completions con imágenes como data-URL + guided_json.

    `images` = [{"label": str, "png": bytes}...] — mismo formato que gemini.
    """
    content: list[dict[str, Any]] = [{"type": "text", "text": user_text}]
    for img in images:
        content.append({"type": "text", "text": img["label"]})
        b64 = base64.b64encode(img["png"]).decode("ascii")
        content.append({"type": "image_url",
                        "image_url": {"url": f"data:image/png;base64,{b64}"}})

    messages: list[dict[str, Any]] = []
    if system_instruction:
        messages.append({"role": "system", "content": system_instruction})
    messages.append({"role": "user", "content": content})

    body: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "temperature": temperature,
    }
    if response_schema is not None:
        body["extra_body"] = {"guided_json": response_schema}

    req = urllib.request.Request(
        f"{base_url.rstrip('/')}/chat/completions",
        data=json.dumps(body, ensure_ascii=False).encode("utf-8"),
        method="POST",
        headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            raw = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        err_body = e.read().decode("utf-8", errors="replace")[:800] if e.fp else ""
        _log.error("VLM local HTTP %s: %s", e.code, err_body)
        raise
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"VLM local no accesible en {base_url} — ¿está levantado el servidor "
            f"vLLM/SGLang? ({e.reason})") from e

    choices = raw.get("choices") or []
    if not choices:
        raise ValueError("VLM local: respuesta sin choices")
    text = (choices[0].get("message") or {}).get("content") or ""
    if not text.strip():
        raise ValueError("VLM local: respuesta vacía")
    return extract_json_from_llm(text)


# --- cable 2: libro de custodia. Toda llamada al modelo pasa por aquí. ---
def generate_json_with_images(*, base_url: str = "http://localhost:8000/v1", user_text: str, images: list, **kw):
    import sys as _sys
    from pathlib import Path as _Path
    from custodia.ledger import record_call, is_local
    payload = user_text + "".join(i.get("label", "") for i in images) + f"|images={len(images)}"
    step = _Path(_sys.argv[0]).stem if _sys.argv and _sys.argv[0] else "unknown"
    with record_call(step=step, engine="vlm_local", endpoint=base_url.split("/")[2] if "//" in base_url else base_url,
                     leaves_machine=not is_local(base_url), payload=payload) as rec:
        out = _generate_json_with_images_raw(base_url=base_url, user_text=user_text, images=images, **kw)
        rec["output_chars"] = len(str(out)) if out is not None else 0
        return out
