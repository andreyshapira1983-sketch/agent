"""Deterministic pre-publish value gate for self-build proposals (TD-035).

The self-build producer's Critic (``core/self_build_producer.py``) checks that a
generated patch is *technically* valid: it parses, is not a diff, is not a
critical file, has tests, and passes the lane risk classifier. It does NOT judge
whether the change is *worth making*. A live run produced a proposal for
``core/redaction.py`` that reached ``committed_local`` and passed tests but was
only a comment-capitalization edit (``WIDEST`` -> ``widest``) dressed up as a
"robustness improvement" — technically valid, zero value.

This module adds a small, deterministic value gate that runs AFTER the Critic
passes and BEFORE the Reporter publishes an approval item. It has two tiers:

* **Hard veto** (``value_veto``): the change has *no code effect* — it is
  whitespace-only, formatting-only, comment-only (Python), or identical after
  normalization. The producer must not publish such a proposal.
* **Soft flag**: the change is published, but a human-visible warning is attached
  (e.g. the summary overclaims value relative to a trivial diff). A soft flag
  never blocks publication and never weakens human approval.

Doc targets (``*.md``) are exempt from hard veto: for a docs file the text *is*
the content, so a text/comment-style change is legitimate. They may still carry
soft flags.

Strictly deterministic and pure: NO LLM call, NO network, NO git, NO file or
inbox side effects. The overclaim signal reuses the deterministic
``core.truth_hype_filter`` (whose ``is_hype`` is a computed property, also no
LLM).
"""
from __future__ import annotations

import io
import tokenize
from dataclasses import dataclass, field
from typing import Any, Callable, Literal

ValueVerdict = Literal["ok", "value_veto"]

# Tokens that carry no code semantics; two sources that differ only in these are
# considered to have "no code effect".
_NONSEMANTIC_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
# Structural tokens whose exact whitespace text is irrelevant (a 4-space vs
# 2-space indent is formatting, not semantics), but whose presence/count is not.
_STRUCTURAL_TOKENS = frozenset(
    {
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
    }
)

# A diff touching at most this many changed lines counts as "trivial" for the
# overclaim soft-flag heuristic.
_TRIVIAL_CHANGED_LINES = 2


@dataclass
class ValueGateResult:
    """Outcome of the value gate. ``verdict == "value_veto"`` means do not
    publish; ``flags`` are human-visible warnings that never block."""

    verdict: ValueVerdict = "ok"
    veto_reasons: list[str] = field(default_factory=list)
    flags: list[str] = field(default_factory=list)

    @property
    def vetoed(self) -> bool:
        return self.verdict == "value_veto"

    def to_dict(self) -> dict[str, Any]:
        return {
            "verdict": self.verdict,
            "veto_reasons": list(self.veto_reasons),
            "flags": list(self.flags),
        }


def _is_doc_target(target: str) -> bool:
    return target.lower().endswith(".md")


def _is_python_target(target: str) -> bool:
    return target.lower().endswith(".py")


def _normalize_ws(text: str) -> str:
    """Canonical form ignoring pure whitespace/blank-line differences.

    Strips each line and drops blank lines. Two sources equal under this form
    differ only in whitespace/formatting layout.
    """
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _python_token_skeleton(text: str) -> tuple | None:
    """A tuple of semantic tokens for Python source, or ``None`` if it will not
    tokenize. Comments and non-logical newlines are dropped; structural tokens
    (INDENT/DEDENT/NEWLINE) keep their identity but not their exact whitespace.

    Two sources with the same skeleton differ only in comments and formatting —
    i.e. they have no code effect.
    """
    skeleton: list[tuple[int, str]] = []
    try:
        for tok in tokenize.generate_tokens(io.StringIO(text).readline):
            if tok.type in _NONSEMANTIC_TOKENS:
                continue
            if tok.type in _STRUCTURAL_TOKENS:
                skeleton.append((tok.type, ""))
            else:
                skeleton.append((tok.type, tok.string))
    except (tokenize.TokenError, IndentationError, SyntaxError, ValueError):
        return None
    return tuple(skeleton)


def _changed_line_count(current: str, proposed: str) -> int:
    """Number of added/removed lines between the two texts (difflib-based)."""
    import difflib

    diff = difflib.ndiff(current.splitlines(), proposed.splitlines())
    return sum(1 for line in diff if line[:1] in ("+", "-"))


def _no_code_effect(target: str, current: str, proposed: str) -> str | None:
    """Return a hard-veto reason if the change has no code effect, else None.

    Only called for non-doc targets. Order: identical-after-normalization,
    Python comment/formatting-only, then generic whitespace-only.
    """
    if _is_python_target(target):
        cur_skel = _python_token_skeleton(current)
        prop_skel = _python_token_skeleton(proposed)
        if (
            cur_skel is not None
            and prop_skel is not None
            and cur_skel == prop_skel
        ):
            return (
                "comment-only or formatting-only Python change "
                "(identical code token skeleton; no code effect)"
            )
        # If either side fails to tokenize we fall through to the whitespace
        # check rather than trusting an unparseable comparison.

    if _normalize_ws(current) == _normalize_ws(proposed):
        return "whitespace-only or formatting-only change (no code effect)"

    return None


def _overclaim_flag(
    current: str,
    proposed: str,
    reason: str,
    *,
    hype_fn: Callable[[str], Any],
) -> str | None:
    """Return a soft-flag string if the summary overclaims value relative to a
    trivial diff, else None. Deterministic (reuses truth_hype_filter)."""
    reason = (reason or "").strip()
    if not reason:
        return None
    changed = _changed_line_count(current, proposed)
    if changed > _TRIVIAL_CHANGED_LINES:
        return None
    try:
        outcome = hype_fn(reason)
    except Exception:  # noqa: BLE001 — the hype filter must never break the gate
        return None
    flag = getattr(outcome, "is_hype", None)
    is_hype = flag() if callable(flag) else bool(flag)
    if is_hype:
        return (
            "summary overclaims value relative to a trivial diff "
            f"({changed} changed line(s)): {reason!r}"
        )
    return None


def evaluate_proposal_value(
    target: str,
    current_content: str,
    proposed_content: str,
    reason: str = "",
    *,
    hype_fn: Callable[[str], Any] | None = None,
) -> ValueGateResult:
    """Evaluate the value of a proposed change to ``target``.

    ``current_content`` / ``proposed_content`` are the full pre/post file bodies.
    ``reason`` is the Builder's one-line summary. ``hype_fn`` defaults to
    :func:`core.truth_hype_filter.evaluate` (deterministic, no LLM); it is
    injectable for tests.

    Returns a :class:`ValueGateResult`. Never raises, never has side effects.
    """
    current = current_content or ""
    proposed = proposed_content or ""

    if hype_fn is None:
        from core.truth_hype_filter import evaluate as hype_fn  # lazy, no cycle

    result = ValueGateResult()

    # ── hard veto: no code effect (doc targets are exempt) ──────────────────
    if not _is_doc_target(target):
        veto_reason = _no_code_effect(target, current, proposed)
        if veto_reason is not None:
            result.verdict = "value_veto"
            result.veto_reasons.append(veto_reason)
            # A no-effect change plus an overclaiming summary is the exact
            # incident shape; surface the overclaim as a flag on the veto too.
            over = _overclaim_flag(current, proposed, reason, hype_fn=hype_fn)
            if over is not None:
                result.flags.append(over)
            return result

    # ── soft flag: overclaiming summary on a trivial diff ───────────────────
    over = _overclaim_flag(current, proposed, reason, hype_fn=hype_fn)
    if over is not None:
        result.flags.append(over)

    return result
