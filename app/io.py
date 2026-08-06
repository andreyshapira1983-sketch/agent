"""Helpers for configuring CLI and subprocess I/O as UTF-8."""
from __future__ import annotations

import os
import sys


def _force_utf8_io() -> tuple[str, ...]:
    """Make REPL safe for non-ASCII input on Windows.

    Python on Windows opens stdin/stdout/stderr in the active console
    code page (cp1251 / cp866 / cp65001 depending on locale + chcp).
    Anything the user types that isn't ASCII therefore round-trips as
    mojibake — and gets persisted to `memory.jsonl` that way too.

    We force UTF-8 explicitly across the three streams. `reconfigure`
    exists on `TextIOWrapper` (Python 3.7+). We also export
    `PYTHONIOENCODING=utf-8` so any subprocess (e.g. shell_exec) inherits
    the same encoding.

    Returns the names of the streams whose encoding IS utf-8 afterwards,
    checked by reading `stream.encoding` back rather than by assuming the
    call worked. A stream can be missing `reconfigure`, refuse it, or
    accept it and stay on its old codec; all three used to look identical
    from outside, and none of them raised.
    """
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    confirmed: list[str] = []
    for stream_name in ("stdin", "stdout", "stderr"):
        stream = getattr(sys, stream_name, None)
        # `reconfigure` is missing on some embedded interpreters and on
        # already-replaced streams (e.g. when pytest captures them). This
        # guard is load-bearing on its own: the handler below is narrow, so a
        # call on `None` would raise TypeError and reach the caller rather
        # than being absorbed.
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is None:
            continue
        try:
            reconfigure(encoding="utf-8", errors="replace")
            if str(getattr(stream, "encoding", "")).lower().replace("-", "") == "utf8":
                confirmed.append(stream_name)
        except (OSError, ValueError, LookupError):
            # Narrow on purpose. A dead stream raises OSError, a closed one
            # ValueError, an unknown codec LookupError — measured, not assumed.
            # A blanket `except Exception` also absorbed the TypeError from
            # calling a missing `reconfigure`, which made the guard above
            # untestable: removing it changed nothing observable.
            #
            # A stream that refuses must not stop startup, so the failure is
            # swallowed. Nothing records it, and nothing can: this runs as the
            # very first statement of `run_cli`, long before the agent and its
            # journal exist — and it cannot depend on them, because it is what
            # prepares the streams the journal will later print through.
            #
            # The caller can see WHICH streams were reconfigured from the
            # return value; that is the only signal available at this point.
            pass  # nosec B110 — the reason is the comment above
    return tuple(confirmed)
