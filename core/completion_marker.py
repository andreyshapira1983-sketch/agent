"""An attempt-bound channel for the synthesizer's completion declaration.

Only a terminal line bearing the CURRENT attempt's nonce is read. Per
attempt rather than per run, so a marker copied out of a retried attempt
cannot validate against the one that was actually banked.
"""
from __future__ import annotations

import re
import secrets
from collections.abc import Iterable
from dataclasses import dataclass

# Anchored to the END of the text, and matched against the ORIGINAL string so
# the body can be recovered by offset rather than rebuilt.
#
# Rebuilding via `splitlines()` + `"\n".join()` + `rstrip()` was the first
# implementation and it silently rewrote the user's answer: CRLF became LF,
# trailing spaces vanished, and a blank line before the marker was eaten. A
# parser that edits bytes it was not asked to edit is a bug the user can see.
#
# The leading group consumes exactly ONE newline — the one that terminates the
# body's last line — or matches the start of the text when the marker is all
# there is. Everything before that offset is returned untouched.
_TERMINAL_MARKER_RE = re.compile(
    r"(?:\r\n|\n|\A)"
    r"\[\[agent\.completion:([0-9a-f]{8,64}):([A-Za-z_]{1,40})\]\]"
    r"[ \t\r\n]*\Z"
)

_SANITISE_RE = re.compile(r"[^a-z0-9_-]+")
_MAX_TOKEN_CHARS = 32

# Parse outcomes. Deliberately three, not two: "no marker at all" and "our
# marker carrying something we do not recognise" call for different reactions —
# the first is a model that ignored the instruction, the second is a model that
# followed it and invented a verdict.
STATUS_OK = "ok"
STATUS_MISSING = "missing"
STATUS_UNPARSED = "unparsed"


@dataclass(frozen=True)
class MarkerParse:
    """What the answer declared, and the text with our line removed.

    `detail` is bounded and sanitised, and is only set for `unparsed`:
    either `wrong_nonce` or the offending token. The nonce itself never
    appears here — it is a secret of the run and must not reach a log.
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


# Only used when a caller does not supply its own vocabulary, so the
# instruction text can still be rendered in isolation (docs, tests).
_DEFAULT_TOKENS = frozenset(
    {"achieved", "partially_achieved", "blocked", "refused", "failed"}
)


def parse_completion_marker(
    text: str, *, nonce: str, valid_tokens: Iterable[str]
) -> MarkerParse:
    """Read and strip our terminal marker; leave everything else untouched.

    1. Only a TERMINAL marker is considered — nothing but whitespace may
    follow it. A marker in the middle of an answer is content: quoted,
    explained, or part of an example. Rewriting the middle of a user's
    answer is not something a parser should ever do. 2. The nonce must be
    the current attempt's. A wrong one is somebody else's line: a stale
    attempt, an echo, or a guess. It is reported, never obeyed, and never
    deleted. 3. A line carrying OUR nonce is ours, so it is stripped even
    when the token is unrecognised — otherwise a model typo would print the
    run's nonce to the user and into stored memory.

    **Byte contract for the returned body.** Removal is done by slicing the
    ORIGINAL string, never by re-joining parsed lines, so the prefix comes
    back byte-for-byte: CRLF stays CRLF, trailing spaces survive, and blank
    lines are preserved. Exactly one newline is consumed — the one that ends
    the body's last line and introduces the marker. So
    `"body\\n\\n<marker>"` returns `"body\\n"`, keeping the blank line the
    author wrote. When nothing is stripped, the very same string object is
    returned.
    """
    if not text:
        return MarkerParse(declared=None, text=text, status=STATUS_MISSING)

    match = _TERMINAL_MARKER_RE.search(text)
    if match is None:
        return MarkerParse(declared=None, text=text, status=STATUS_MISSING)

    found_nonce, raw_token = match.group(1), match.group(2)
    if not nonce or found_nonce != nonce:
        # Not ours. Report it, keep the text exactly as written.
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
