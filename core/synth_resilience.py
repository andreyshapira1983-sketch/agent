"""Synthesizer resilience ladder.

The synthesizer is the single LLM call that turns collected evidence into the
user-facing Output Contract answer. Historically a model error there had no
recovery path: the model router only fails over on key/quota/auth errors, and
the call site caught only ``ModelBudgetExceeded``. Any other provider error —
e.g. a ``400 BadRequest: Could not finish the message`` — propagated straight
up, crashing the whole turn (or, inside a sub-agent, demoting it to an error).

This module implements the graded recovery a resilient agent should have,
without re-executing any side effects (synthesis is a pure model call):

    failure 1  -> ordinary retry (same request; handles a transient blip)
    failure 2  -> adapted retry  (shrunk context/output; handles a request that
                  was too large or that the model "could not finish")
    failure 3  -> stop: return an honest, Output-Contract-shaped degraded answer
                  instead of a fourth identical attempt or a crash.

``ModelBudgetExceeded`` (and any other caller-supplied *fatal* type) is never
retried — it must propagate so the budget-pause checkpoint fires.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Sequence


def classify_model_error(exc: BaseException) -> str:
    """Coarse class of a model-call failure, for logging and retry policy.

    Distinguishes a *repeatable request* problem (400/422 — retrying the exact
    same request will fail again, so adaptation is required) from a *transient*
    one (429/5xx/timeout — a plain retry may well succeed).
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    try:
        status_int = int(status) if status is not None else None
    except (TypeError, ValueError):
        status_int = None

    name = type(exc).__name__.lower()
    text = str(exc).lower()

    if status_int in (429,) or "ratelimit" in name or "rate limit" in text or "too many requests" in text:
        return "rate_limit"
    if "timeout" in name or "timedout" in name or "timed out" in text:
        return "timeout"
    if status_int is not None and 500 <= status_int <= 599:
        return "server_error"
    if "serviceunavailable" in name or "internalserver" in name or "apiconnection" in name:
        return "server_error"
    if status_int in (400, 422) or "badrequest" in name or "invalidrequest" in name:
        return "bad_request"
    if "could not finish" in text or "unprocessable" in text:
        return "bad_request"
    return "unknown"


def is_transient_error(exc: BaseException) -> bool:
    """True when a plain retry (no adaptation) is worth attempting."""
    return classify_model_error(exc) in {"rate_limit", "timeout", "server_error", "unknown"}


@dataclass(frozen=True)
class SynthAttempt:
    """Describes how the caller should build the synthesis for one attempt."""
    index: int            # 0-based attempt number
    adapt_context: bool   # shrink prompt/output (lean context) for this attempt
    is_final: bool        # last attempt before degrading


@dataclass(frozen=True)
class SynthLadderResult:
    answer: str
    attempts: int         # how many synthesis attempts were made
    degraded: bool        # True when every attempt failed and answer is the honest partial
    errors: tuple[str, ...]
    final_error_class: str | None


def run_synthesizer_ladder(
    do_synthesize: Callable[[SynthAttempt], str],
    *,
    build_degraded_answer: Callable[[Sequence[str], str | None], str],
    on_event: Callable[[str, dict[str, Any]], None] = lambda *_a, **_k: None,
    fatal_types: tuple[type[BaseException], ...] = (),
    max_attempts: int = 3,
) -> SynthLadderResult:
    """Drive the retry -> adapt -> honest-partial ladder around ``do_synthesize``.

    ``do_synthesize`` receives a :class:`SynthAttempt` and must return the draft
    answer or raise. It MUST NOT perform side effects — it is called up to
    ``max_attempts`` times. ``fatal_types`` (e.g. ``ModelBudgetExceeded``) are
    re-raised immediately, never retried.
    """
    max_attempts = max(1, int(max_attempts))
    errors: list[str] = []
    last_class: str | None = None

    for index in range(max_attempts):
        is_final = index == max_attempts - 1
        # Only the last attempt adapts the request: failure 1 -> plain retry,
        # failure 2 -> adapted retry, which is also the final one before we
        # degrade. This guarantees we never repeat the exact same request more
        # than once and never fire a fourth attempt.
        attempt = SynthAttempt(
            index=index,
            adapt_context=is_final and max_attempts > 1,
            is_final=is_final,
        )
        try:
            answer = do_synthesize(attempt)
        except fatal_types:
            raise
        except Exception as exc:  # noqa: BLE001 — recovery boundary
            err = f"{type(exc).__name__}: {exc}"
            last_class = classify_model_error(exc)
            errors.append(err)
            on_event(
                "synthesizer_attempt_failed",
                {
                    "attempt": index + 1,
                    "max_attempts": max_attempts,
                    "error_class": last_class,
                    "error": err[:300],
                    "will_retry": not is_final,
                    "next_strategy": (
                        "degrade" if is_final
                        else ("adapt_context" if index + 1 == max_attempts - 1 else "retry")
                    ),
                },
            )
            continue
        # Success.
        if index > 0:
            on_event(
                "synthesizer_recovered",
                {"attempt": index + 1, "adapted": attempt.adapt_context},
            )
        return SynthLadderResult(
            answer=answer,
            attempts=index + 1,
            degraded=False,
            errors=tuple(errors),
            final_error_class=None,
        )

    degraded = build_degraded_answer(errors, last_class)
    on_event(
        "synthesizer_degraded",
        {
            "attempts": len(errors),
            "final_error_class": last_class,
            "errors": [e[:300] for e in errors],
        },
    )
    return SynthLadderResult(
        answer=degraded,
        attempts=len(errors),
        degraded=True,
        errors=tuple(errors),
        final_error_class=last_class,
    )


def build_degraded_synthesis_answer(
    errors: Sequence[str],
    error_class: str | None,
) -> str:
    """An honest, Output-Contract-shaped answer used when synthesis exhausts.

    It carries no invented facts and marks the missing answer as Unverified, so
    the verifier records it as a low-quality / partial turn rather than a
    confident success.
    """
    last = errors[-1] if errors else "unknown model error"
    cls = error_class or "unknown"
    return (
        "## Conclusion\n"
        "I could not produce the final answer this turn: the synthesizer model "
        f"failed on every attempt ({len(errors)}). The evidence was collected, but "
        "turning it into a grounded answer did not complete.\n\n"
        "## Facts\n"
        f"- The synthesizer failed {len(errors)} time(s); last error class: {cls}.\n\n"
        "## Unverified\n"
        "- The full answer to your question — synthesis did not finish, so no "
        "grounded response can be shown yet.\n\n"
        "## Confidence\n"
        "low\n\n"
        f"<!-- synthesizer_degraded: {last[:200]} -->"
    )
