"""
Multimodal input: text, images, audio. MVP: text only.
"""
from __future__ import annotations

from typing import Any


def parse_input(raw: dict[str, Any]) -> dict[str, Any]:
    text = (raw.get("text") or raw.get("message") or "").strip()
    return {"text": text, "type": "text"}
