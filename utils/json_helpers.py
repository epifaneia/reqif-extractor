"""Helpers para parsear respuestas JSON de LLMs y construir claves de deduplicación."""

from __future__ import annotations

import json
import re
from typing import Any


def extract_json_from_llm(text: str) -> Any:
    """
    Extrae el primer objeto/array JSON válido de una respuesta LLM.

    Maneja los casos más comunes de salida de modelos:
    - JSON puro.
    - JSON envuelto en fences markdown ```json ... ```.
    - JSON con texto narrativo antes o después.
    - JSON con backticks simples.

    Lanza json.JSONDecodeError si no encuentra nada parseable.
    """
    text = text.strip()

    # Quitar fences markdown ```json ... ``` o ``` ... ```
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text)
        text = re.sub(r"\s*```\s*$", "", text, flags=re.DOTALL)
        text = text.strip()

    # Intento directo
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Buscar primer { o [ y extraer hasta el cierre balanceado
    for open_c, close_c in (("{", "}"), ("[", "]")):
        start = text.find(open_c)
        if start == -1:
            continue
        end = text.rfind(close_c)
        if end <= start:
            continue
        try:
            return json.loads(text[start : end + 1])
        except json.JSONDecodeError:
            continue

    raise json.JSONDecodeError("No se encontró JSON válido en la respuesta LLM", text, 0)


def build_dedup_key(*fields: Any) -> str:
    """
    Clave de deduplicación None-safe para referencias extraídas.

    Cada campo None se convierte en cadena vacía (no en "none"),
    evitando que referencias sin código colapsen entre sí de forma incorrecta.

    Uso:
        key = build_dedup_key(ref["doc_code"], ref["doc_name"], ref.get("version_date"))
    """
    parts: list[str] = []
    for f in fields:
        if f is None:
            parts.append("")
        else:
            parts.append(str(f).strip().lower())
    return "|".join(parts)
