"""Detect a request to demonstrate an ability by doing it, not by listing it."""
from __future__ import annotations

import re

_PROVE_RE = re.compile(
    r"докаж|продемонстрир|демонстрир|на\s+(практике|деле)|делом\b"
    r"|\bprove\b|\bdemonstrate|\bin\s+practice\b",
    re.IGNORECASE,
)
_SHOW_RE = re.compile(r"покаж|\bshow\s+(me|it|us)\b", re.IGNORECASE)
_ABILITY_RE = re.compile(
    r"уме(ешь|ет|ю)|мож(ешь|ет)|способ(ен|на|ны)|\bcan\s+you\b|\bare\s+you\s+able\b",
    re.IGNORECASE,
)


def demands_demonstration(text: str) -> bool:
    """True when the turn asks to show an ability in action rather than describe it."""
    t = text or ""
    if _PROVE_RE.search(t):
        return True
    return bool(_SHOW_RE.search(t) and _ABILITY_RE.search(t))
