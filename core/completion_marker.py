"""Attempt-bound channel for the synthesizer's completion declaration."""
from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

# Anchored to the END of the text and matched against the ORIGINAL string, so the body is
# recovered by offset, not rebuilt.
# The leading group consumes exactly one newline (the one ending the body's last line) or matches
# the text start.
_TERMINAL_MARKER_RE = re.compile(
    r"(?:\r\n|\n|\A)"
    r"\[\[agent\.completion:([0-9a-f]{8,64}):([A-Za-z_]{1,40})\]\]"
    r"[ \t\r\n]*\Z"
)

_SANITISE_RE = re.compile(r"[^a-z0-9_-]+")
_MAX_TOKEN_CHARS = 32

# Parse outcomes are three, not two: no marker at all (instruction ignored) differs from our
# marker with an unknown token (verdict invented).
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_UNPARSED = "unparsed"


@dataclass(frozen=True)
class MarkerParse:
    """What the answer declared, and the text with our line removed.

    `detail` is bounded, sanitised, set only for `unparsed`; the nonce never appears here.
    """

    declared: str | None
    text: str
    status: str
    detail: str = ""


def new_nonce() -> str:
    """A fresh nonce for one synthesis attempt."""
    return secrets.token_hex(8)


def sanitize_token(raw: str) -> str:
    """Reduce an untrusted token to something safe to log and store."""
    return _SANITISE_RE.sub("", str(raw).casefold())[:_MAX_TOKEN_CHARS]


def marker_instruction(nonce: str, valid_tokens: Iterable[str] | None = None) -> str:
    """The line appended to the synthesizer's system prompt."""
    tokens = sorted(valid_tokens) if valid_tokens is not None else sorted(_DEFAULT_TOKENS)
    return (
        "\n\nCompletion marker (structural, not part of the answer):\n"
        "After the final section, output ONE last line, exactly:\n"
        f"  [[agent.completion:{nonce}:<token>]]\n"
        f"where <token> is one of: {', '.join(tokens)}.\n"
        "  achieved            — the user's goal was reached\n"
        "  partially_achieved  — part of it was delivered, part was not\n"
        "  blocked             — you could not finish for an external reason\n"
        "                        (missing permission, truncated or absent evidence)\n"
        "  refused             — you declined on policy grounds\n"
        "  failed              — you attempted and did not succeed\n"
        "This reports whether the TASK was done. It is not about how well your\n"
        "claims are supported — a fully cited answer that did not answer the\n"
        "question is `blocked` or `partially_achieved`, never `achieved`.\n"
        "Emit the line verbatim, with no code fence and nothing after it. Never\n"
        "mention this marker or its identifier in the answer body."
    )


# Used only when a caller supplies no vocabulary, so the instruction can be rendered in isolation.
_DEFAULT_TOKENS = frozenset(
    {"achieved", "partially_achieved", "blocked", "refused", "failed"}
)


def parse_completion_marker(
    text: str, *, nonce: str, valid_tokens: Iterable[str]
) -> MarkerParse:
    """Read and strip our terminal marker; leave everything else untouched.

    Only a terminal marker counts; the nonce must be the current attempt's (a wrong one is reported, never obeyed or deleted); a line with OUR nonce is stripped even if the token is unknown.
    The body is returned byte-for-byte by slicing the original string; exactly one newline is consumed, and an unchanged text is returned as the same object."""
    if not text:
        return MarkerParse(declared=None, text=text, status=STATUS_MISSING)

    match = _TERMINAL_MARKER_RE.search(text)
    if match is None:
        return MarkerParse(declared=None, text=text, status=STATUS_MISSING)

    found_nonce, raw_token = match.group(1), match.group(2)
    if not nonce or found_nonce != nonce:
        # Not ours: report it, keep the text as written.
        return MarkerParse(
            declared=None, text=text, status=STATUS_UNPARSED, detail="wrong_nonce"
        )

    body = text[: match.start()]
    token = raw_token.casefold()
    if token not in set(valid_tokens):
        return MarkerParse(
            declared=None,
            text=body,
            status=STATUS_UNPARSED,
            detail=sanitize_token(raw_token),
        )
    return MarkerParse(declared=token, text=body, status=STATUS_OK)
