"""Universal redaction layer (§7).

Three surfaces MUST never receive raw secrets:

  1. JSONL trace logs   — `TraceLogger` runs every payload through `redact_payload`.
  2. LLM prompts        — `LLMPlanner` and `AgentLoop._synthesize` redact
                          `user` text before calling `LLM.complete(...)`.
  3. User-facing output — `AgentLoop.run` redacts the final answer string.

The redactor is intentionally NON-reversible: once a secret is replaced
with `[REDACTED:<kind>]`, the kernel does not keep a mapping back. The
raw value only ever exists inside `SecretFinding.matched`, which lives
on the stack and is discarded after the redaction call returns.

Why not stream-level redaction? Because we want classification to see the
raw text first (so `data_classifier` can decide DataClass.SECRET) and
*then* call the redactor before anything leaves the kernel boundary.
"""
from __future__ import annotations

from typing import Any

from core.secret_scanner import SecretFinding, scan


def _replacement(kind: str) -> str:
    return f"[REDACTED:{kind}]"


def redact_text(text: str) -> tuple[str, list[SecretFinding]]:
    """Replace every secret span with a `[REDACTED:<kind>]` token.

    Returns (redacted_text, findings). Overlapping matches are resolved
    by sorting hits by start position descending and skipping any range
    fully contained inside an earlier (later-replaced) range. This keeps
    offsets stable during substitution.
    """
    if not isinstance(text, str) or not text:
        return text, []

    findings = scan(text)
    if not findings:
        return text, []

    # Sort by start ascending, then end descending so the WIDEST match at
    # each starting offset wins. We then replace right-to-left so earlier
    # offsets are not invalidated by replacements that happen after them.
    findings_sorted = sorted(findings, key=lambda f: (f.start, -f.end))
    kept: list[SecretFinding] = []
    last_end = -1
    for f in findings_sorted:
        # Skip a finding whose entire span is already covered by a wider
        # match starting at the same or earlier position.
        if f.start < last_end:
            continue
        kept.append(f)
        last_end = f.end

    # Apply replacements right-to-left.
    out = text
    for f in sorted(kept, key=lambda f: f.start, reverse=True):
        out = out[: f.start] + _replacement(f.kind) + out[f.end :]
    return out, kept


def redact_payload(obj: Any) -> Any:
    """Deep-walk a logging payload, redacting every string field.

    Works on the JSON-serialisable shape that `TraceLogger._serialize`
    produces: dict / list / tuple / scalars. Pydantic models should be
    dumped first (logger already does that), then passed in here.
    """
    if isinstance(obj, str):
        red, _ = redact_text(obj)
        return red
    if isinstance(obj, dict):
        return {k: redact_payload(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        redacted = [redact_payload(x) for x in obj]
        return tuple(redacted) if isinstance(obj, tuple) else redacted
    return obj


def collect_findings(text: str) -> list[SecretFinding]:
    """Convenience for callers that need the findings without rewriting text."""
    if not isinstance(text, str):
        return []
    return scan(text)
