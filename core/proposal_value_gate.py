"""Deterministic pre-publish value gate for self-build proposals: hard veto for changes with no code effect, soft flags for overclaiming summaries."""
# The producer's Critic checks technical validity only; this gate judges whether the change is worth making.
# A soft flag never blocks publication and never weakens human approval.
from __future__ import annotations

import io
import tokenize
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Literal

ValueVerdict = Literal["ok", "value_veto"]

# Tokens with no code semantics; sources differing only in these have "no code effect".
_NONSEMANTIC_TOKENS = frozenset(
    {
        tokenize.COMMENT,
        tokenize.NL,
        tokenize.ENCODING,
        tokenize.ENDMARKER,
    }
)
# Structural tokens whose exact whitespace is irrelevant (indent width) but whose presence and count are not.
_STRUCTURAL_TOKENS = frozenset(
    {
        tokenize.INDENT,
        tokenize.DEDENT,
        tokenize.NEWLINE,
    }
)

# A diff touching at most this many lines is "trivial" for the overclaim soft flag.
_TRIVIAL_CHANGED_LINES = 2


@dataclass
class ValueGateResult:
    """Value gate outcome: verdict "value_veto" means do not publish; flags are human-visible warnings that never block."""

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
    """Canonical form ignoring whitespace and blank-line differences (strip each line, drop blank lines)."""
    return "\n".join(line.strip() for line in text.splitlines() if line.strip())


def _python_token_skeleton(text: str) -> tuple | None:
    """Tuple of semantic tokens of Python source, or None if it will not tokenize; comments and non-logical newlines dropped, so an equal skeleton means no code effect."""
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
    """Hard-veto reason if the change has no code effect (identical, Python comment/formatting-only, whitespace-only), else None; non-doc targets only."""
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
        # If either side fails to tokenize, fall through to the whitespace check.

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
    """Soft-flag string if the summary overclaims value relative to a trivial diff, else None (reuses truth_hype_filter)."""
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
    """Evaluate the value of a proposed change to target; returns a ValueGateResult, never raises."""
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
            # No-effect change plus an overclaiming summary: surface the overclaim as a flag on the veto too.
            over = _overclaim_flag(current, proposed, reason, hype_fn=hype_fn)
            if over is not None:
                result.flags.append(over)
            return result

    # ── soft flag: overclaiming summary on a trivial diff ───────────────────
    over = _overclaim_flag(current, proposed, reason, hype_fn=hype_fn)
    if over is not None:
        result.flags.append(over)

    return result
