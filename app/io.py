"""Helpers for configuring CLI and subprocess I/O as UTF-8."""
from __future__ import annotations

import os
import sys


def _force_utf8_io() -> tuple[str, ...]:
    """Make REPL safe for non-ASCII input on Windows; return the streams confirmed as UTF-8."""
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    confirmed: list[str] = []
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        # `reconfigure` is missing on embedded interpreters and replaced streams (pytest
        # capture); the narrow handler below would not catch the TypeError of calling None.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
            if str(getattr(stream, "encoding", "")).lower().replace("-", "") == "utf8":
                confirmed.append(stream_name)
        except (OSError, ValueError, LookupError):
            # Dead, closed or unknown-codec stream must not stop startup; no journal exists
            # yet (this runs first in run_cli), so the return value is the only signal.
            pass  # nosec B110 — the reason is the comment above
    return tuple(confirmed)
