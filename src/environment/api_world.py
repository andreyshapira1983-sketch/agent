"""
Single point for external API calls. MVP: GET with timeout and max response size.
Max size can be overridden by caller (e.g. from config).
"""
from __future__ import annotations

import urllib.request
from typing import Any, cast

# Default cap (1 MiB); override via max_response_bytes argument.
MAX_RESPONSE_BYTES = 1 * 1024 * 1024


def request(
    method: str,
    url: str,
    json: dict[str, Any] | None = None,
    *,
    max_response_bytes: int | None = None,
) -> str:
    if method.upper() == "GET":
        cap = max_response_bytes if max_response_bytes is not None else MAX_RESPONSE_BYTES
        with urllib.request.urlopen(url, timeout=10) as r:  # nosec B310 — controlled GET URL
            raw = r.read(cap + 1)
            if len(raw) > cap:
                raw = raw[:cap]
            return cast(str, raw.decode("utf-8", errors="replace"))
    return "Placeholder: only GET implemented"
