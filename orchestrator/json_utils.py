"""Robust extraction of a JSON object from model output."""

from __future__ import annotations

import json
from typing import Any, Dict


class JSONExtractionError(ValueError):
    pass


def extract_json_object(text: str) -> Dict[str, Any]:
    """Return the first top-level JSON object found in ``text``.

    Handles ```json fences and leading/trailing prose. Raises JSONExtractionError
    instead of guessing, so callers can fail closed.
    """
    if not text or not text.strip():
        raise JSONExtractionError("empty model output")

    candidate = text.strip()
    if "```" in candidate:
        parts = candidate.split("```")
        for part in parts[1::2]:
            body = part[4:] if part.lower().startswith("json") else part
            body = body.strip()
            if body.startswith("{"):
                candidate = body
                break

    start = candidate.find("{")
    if start == -1:
        raise JSONExtractionError("no JSON object in model output")

    depth = 0
    in_string = False
    escape = False
    for idx in range(start, len(candidate)):
        ch = candidate[idx]
        if in_string:
            if escape:
                escape = False
            elif ch == "\\":
                escape = True
            elif ch == '"':
                in_string = False
            continue
        if ch == '"':
            in_string = True
        elif ch == "{":
            depth += 1
        elif ch == "}":
            depth -= 1
            if depth == 0:
                try:
                    obj = json.loads(candidate[start : idx + 1])
                except json.JSONDecodeError as exc:
                    raise JSONExtractionError(f"invalid JSON: {exc}") from exc
                if not isinstance(obj, dict):
                    raise JSONExtractionError("top-level JSON is not an object")
                return obj
    raise JSONExtractionError("unterminated JSON object")
