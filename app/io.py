"""CLI/console I/O helpers (encoding, startup notices)."""
from __future__ import annotations

import os
import sys


def _force_utf8_io() -> None:
    """Make REPL safe for non-ASCII input on Windows.

    Python on Windows opens stdin/stdout/stderr in the active console
    code page (cp1251 / cp866 / cp65001 depending on locale + chcp).
    Anything the user types that isn't ASCII therefore round-trips as
    mojibake — and gets persisted to `memory.jsonl` that way too.

    We force UTF-8 explicitly across the three streams. `reconfigure`
    exists on `TextIOWrapper` (Python 3.7+). We also export
    `PYTHONIOENCODING=utf-8` so any subprocess (e.g. shell_exec) inherits
    the same encoding.
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        # `reconfigure` is missing on some embedded interpreters and on
        # already-replaced streams (e.g. when pytest captures them).
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            # Best-effort: if a stream refuses (e.g. piped from a process
            # that already mangled bytes upstream) we keep going rather
            # than crash the REPL. The audit log will still capture the
            # raw bytes for forensics.
            pass


if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8")
        sys.stderr.reconfigure(encoding="utf-8")
    except Exception:
        pass
