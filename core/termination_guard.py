"""Termination awareness — addresses MAST FM-1.5 and FM-3.1.

Two observational checks that flag the loop when it should *recognize* a
terminal state but might not:

* **Stagnation** (FM-1.5, "unaware of termination", 12.4% of failures —
  almost exclusively in failed runs per UC Berkeley 2025).  If two
  consecutive replan attempts produce the same failure-code signature
  AND the same set of usable artifacts, the loop is looping.  We surface
  this as ``stagnation_detected`` so an operator (or a future stricter
  policy) can stop early instead of burning the rest of the attempt
  budget on the same broken plan.

* **Premature completion** (FM-3.1, 6.2%).  If the loop is about to
  return an answer with an empty evidence chain on a question that
  appears to *require* tools (looking-up files, running tests, reading
  logs, fetching the web), we surface ``premature_completion_risk``.
  The synthesizer still runs — this is a signal, not a block — but the
  event makes the failure mode auditable.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable


# Question keywords (EN + RU) that strongly suggest the answer needs a
# fresh tool call rather than general-knowledge synthesis.  Keep tight
# to avoid flagging philosophical / definition-style questions.
_TOOL_DEMANDING_KEYWORDS: tuple[str, ...] = (
    # File / repo
    "open ", "read ", "show me", "look at", "содерж", "прочит", "покаж",
    "что в файле", "what's in", "what is in",
    # Tests / build
    "run the test", "запусти тест", "прогон", "pytest",
    # Logs
    "in the log", "из лога", "в логах", "журнал",
    # Web
    "search for", "найди в интерн", "google", "fetch", "load url",
    # Diff / changes
    "diff ", "разниц", "что измен", "what changed",
    # Shell
    "execute ", "выполни команд",
)


@dataclass(frozen=True)
class StagnationEvent:
    """Same failure signature observed in two adjacent attempts."""

    attempt: int
    repeat_count: int  # how many consecutive matching attempts
    failure_codes: tuple[str, ...]
    artifact_labels: tuple[str, ...]

    def to_log_payload(self) -> dict:
        return {
            "attempt": self.attempt,
            "repeat_count": self.repeat_count,
            "failure_codes": list(self.failure_codes),
            "artifact_labels": list(self.artifact_labels),
        }


@dataclass(frozen=True)
class PrematureCompletionEvent:
    """About to answer with no evidence on a question that wanted some."""

    matched_keywords: tuple[str, ...]
    chain_size: int

    def to_log_payload(self) -> dict:
        return {
            "matched_keywords": list(self.matched_keywords),
            "chain_size": self.chain_size,
        }


def _signature(
    failure_codes: Iterable[str], artifact_labels: Iterable[str]
) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """Stable ordered representation suitable for equality comparison."""
    return (
        tuple(sorted(c for c in failure_codes if c)),
        tuple(sorted(a for a in artifact_labels if a)),
    )


class TerminationGuard:
    """Per-run stagnation tracker + completion-risk checker."""

    def __init__(self) -> None:
        self._last_signature: tuple[tuple[str, ...], tuple[str, ...]] | None = None
        self._repeat_count: int = 1
        self._reported: bool = False

    def observe_attempt(
        self,
        *,
        attempt: int,
        failure_codes: Iterable[str],
        artifact_labels: Iterable[str],
    ) -> StagnationEvent | None:
        """Record one attempt's outcome and return an event the FIRST time
        the same (failures, artifacts) signature is seen twice in a row.

        Subsequent identical attempts in the same chain do not re-fire to
        keep the log signal a single edge, not a stream.  Resetting to a
        different signature re-arms the detector.
        """
        sig = _signature(failure_codes, artifact_labels)
        if self._last_signature is not None and sig == self._last_signature:
            self._repeat_count += 1
            if self._repeat_count >= 2 and not self._reported:
                self._reported = True
                return StagnationEvent(
                    attempt=attempt,
                    repeat_count=self._repeat_count,
                    failure_codes=sig[0],
                    artifact_labels=sig[1],
                )
        else:
            self._last_signature = sig
            self._repeat_count = 1
            self._reported = False
        return None

    def check_completion(
        self,
        *,
        question: str,
        chain_size: int,
        had_any_artifacts: bool,
    ) -> PrematureCompletionEvent | None:
        """Flag premature completion: empty evidence on a tool-demanding
        question, with no artifacts produced anywhere in the run.

        Question-class detection is intentionally narrow (a few high-
        signal verbs) so generic conversation and definition lookups
        don't trip the alarm.
        """
        if had_any_artifacts or chain_size > 0:
            return None
        text = (question or "").lower()
        if not text:
            return None
        matched = tuple(kw for kw in _TOOL_DEMANDING_KEYWORDS if kw in text)
        if not matched:
            return None
        return PrematureCompletionEvent(
            matched_keywords=matched,
            chain_size=chain_size,
        )
