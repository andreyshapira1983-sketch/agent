"""Compact view of a JSON response, so short facts are not buried behind long fields."""
from __future__ import annotations

import json
import re
from typing import Any

MAX_STRING = 300
MAX_LIST = 20
MAX_KEYS = 50
_SEP = re.compile(r"\s*,?\s*")
_COLON = re.compile(r"\s*:\s*")


def _complete_members(text: str) -> dict[str, Any] | None:
    """Complete top-level members of a JSON object whose body was cut mid-way."""
    s = text.lstrip()
    if not s.startswith("{"):
        return None
    decoder, i, out = json.JSONDecoder(), 1, {}
    while True:
        i = _SEP.match(s, i).end()
        if i >= len(s) or s[i] == "}":
            break
        try:
            key, i = decoder.raw_decode(s, i)
            i = _COLON.match(s, i).end()
            value, i = decoder.raw_decode(s, i)
        except ValueError:
            break
        out[str(key)] = value
    return out or None


def _clip(value: Any) -> Any:
    if isinstance(value, str):
        return value if len(value) <= MAX_STRING else f"{value[:MAX_STRING]}… [+{len(value) - MAX_STRING} chars]"
    if isinstance(value, dict):
        if len(value) > MAX_KEYS:
            return {"[keys]": list(value), "[count]": len(value)}
        return {k: _clip(v) for k, v in value.items()}
    if isinstance(value, list):
        head = [_clip(v) for v in value[:MAX_LIST]]
        return head + ([f"… [+{len(value) - MAX_LIST} items]"] if len(value) > MAX_LIST else [])
    return value


def compact_json_view(text: str, *, truncated: bool) -> str | None:
    """Return the compact view, or None if the text is not a JSON object or array."""
    try:
        data, whole = json.loads(text), True
    except ValueError:
        data, whole = _complete_members(text), False
    if not isinstance(data, (dict, list)):
        return None
    notes = [(f"strings over {MAX_STRING} chars, lists over {MAX_LIST} items and "
              f"objects over {MAX_KEYS} keys are clipped")]
    if truncated or not whole:
        notes.append("the body was cut at the size limit: only complete top-level members are shown")
    return f"JSON, compact view ({'; '.join(notes)}):\n" + json.dumps(_clip(data), ensure_ascii=False, indent=1)
