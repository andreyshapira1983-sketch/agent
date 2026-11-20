"""Termination awareness (MAST FM-1.5, FM-3.1): two observational checks that flag the loop when it should recognise a terminal state.

Stagnation: two consecutive replan attempts with the same failure-code signature AND the same usable artifacts mean the loop is looping; surfaced as ``stagnation_detected``. Premature completion: answering with an empty evidence chain on a question that seems to require tools; surfaced as ``premature_completion_risk``. Signals, not blocks.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

# Question keywords (EN + RU) suggesting the answer needs a fresh tool call; kept tight to avoid flagging definition-style questions.
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
        """Record one attempt's outcome and return an event the FIRST time the
        same (failures, artifacts) signature is seen twice in a row.
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
