"""Detect disagreements between cognitive subsystems on the same turn.

A "disagreement" is when two subsystems produce contradictory verdicts
about the same answer/turn. Examples:

* The **planner** marked all steps `done` (i.e. the answer-assembly
  pipeline reported success), but the **verifier** returned
  ``fully_unverified=True`` — the answer was synthesised but no claim
  resolved to a real source.
* The **planner** chose tools to gather sources, but the **executor**
  produced no artifacts (every tool call failed or was skipped).
* A significant fraction of claims carry citations that the verifier
  could not match (``cited_but_unmatched``) — the planner thought the
  needed source was in scope but verification said otherwise.

This module is a pure function: it takes already-computed subsystem
outputs and returns zero or more event payloads. The `loop.py`
integration logs each one as a ``subsystem_disagreement`` JSONL event
right after verification, so operators can see "органы системы"
contradicting each other in the trace.

Logging only — no behaviour change. A future iteration may feed these
events back into ReplanPolicy or the confidence vector.
"""
from __future__ import annotations

from collections.abc import Sequence
from typing import Any

# Threshold for "partial" planner/verifier disagreement: when more than
# this fraction of claim chunks carry citations the verifier could not
# match, we count it as a disagreement event. Tuned conservatively so a
# single broken citation in a long answer does not spam the trace.
_PARTIAL_UNMATCH_THRESHOLD = 0.30


def _plan_step_counts(plan_steps: Sequence[Any]) -> tuple[int, int, int]:
    """Return (total, done, failed) over a plan's steps.

    Each step is expected to expose a ``status`` attribute in
    ``{"pending", "in_progress", "done", "failed"}``. Unknown statuses
    (e.g. tests passing dicts) are tolerated by reading ``.get`` first.
    """
    total = len(plan_steps)
    done = 0
    failed = 0
    for s in plan_steps:
        status = getattr(s, "status", None)
        if status is None and isinstance(s, dict):
            status = s.get("status")
        if status == "done":
            done += 1
        elif status == "failed":
            failed += 1
    return total, done, failed


def detect_disagreements(
    *,
    attempt: int,
    plan_steps: Sequence[Any],
    artifacts: dict[str, Any] | None,
    report: Any,
    failure_history: Sequence[Any] | None = None,
) -> list[dict[str, Any]]:
    """Return zero or more `subsystem_disagreement` event payloads.

    Each payload is a flat ``dict`` ready to pass to ``log.log``.

    Cases v1:

    * ``planner_vs_verifier_full``: planner reports all-done, verifier
      reports ``fully_unverified``. Severity: ``high``.
    * ``planner_vs_verifier_partial``: planner reports all-done, but
      ``cited_but_unmatched_chunks / total_chunks > threshold``.
      Severity: ``medium``.
    * ``planner_vs_executor``: planner has steps but no artifact was
      produced — every tool call failed or was skipped. Severity:
      ``low`` (could be a legitimate general-knowledge answer).
    """
    events: list[dict[str, Any]] = []

    total, done, failed = _plan_step_counts(plan_steps)
    artifacts_count = len(artifacts) if artifacts else 0
    all_done = total > 0 and done == total
    failure_codes = (
        [getattr(t, "code", None) for t in failure_history]
        if failure_history else []
    )
    failure_codes = [c for c in failure_codes if c]

    base = {
        "attempt": attempt,
        "phase": "verify",
        "planner_steps_total": total,
        "planner_steps_done": done,
        "planner_steps_failed": failed,
        "artifacts_count": artifacts_count,
        "failure_codes": failure_codes,
    }

    # Verifier counters (None when verifier disabled / no report given).
    if report is not None:
        verified = getattr(report, "verified_chunks", 0) or 0
        unmatched = getattr(report, "cited_but_unmatched_chunks", 0) or 0
        total_chunks = getattr(report, "total_chunks", 0) or 0
        fully_unverified = bool(getattr(report, "fully_unverified", False))
        chain_empty = bool(getattr(report, "chain_was_empty", False))
        base.update({
            "verifier_total_chunks": total_chunks,
            "verifier_verified_chunks": verified,
            "verifier_cited_but_unmatched_chunks": unmatched,
            "verifier_fully_unverified": fully_unverified,
            "verifier_chain_was_empty": chain_empty,
        })

        # Case 1: full planner/verifier disagreement.
        # Suppress when the chain was empty AND no tools were chosen —
        # that is a pure-prior-knowledge answer, not a disagreement
        # (verifier honestly says "no source", planner honestly didn't
        # plan one). The disclaimer pathway already covers it.
        if all_done and fully_unverified and total_chunks > 0:
            if not (chain_empty and artifacts_count == 0):
                events.append({
                    **base,
                    "kind": "planner_vs_verifier_full",
                    "subsystems": ["planner", "verifier"],
                    "severity": "high",
                    "description": (
                        "Planner reported all steps done but verifier "
                        "found the answer fully unverified."
                    ),
                })

        # Case 2: partial mismatch on citations.
        elif (
            all_done
            and total_chunks > 0
            and unmatched / total_chunks > _PARTIAL_UNMATCH_THRESHOLD
        ):
            events.append({
                **base,
                "kind": "planner_vs_verifier_partial",
                "subsystems": ["planner", "verifier"],
                "severity": "medium",
                "description": (
                    "Planner reported all steps done but a significant "
                    "fraction of claims cite sources the verifier could "
                    "not match."
                ),
                "unmatched_ratio": round(unmatched / total_chunks, 3),
                "threshold": _PARTIAL_UNMATCH_THRESHOLD,
            })

    # Case 3: planner chose tools, executor produced nothing.
    if total > 0 and artifacts_count == 0 and done == 0 and failed > 0:
        events.append({
            **base,
            "kind": "planner_vs_executor",
            "subsystems": ["planner", "executor"],
            "severity": "low",
            "description": (
                "Planner produced steps but no artifact was emitted; "
                "every tool call failed or was skipped."
            ),
        })

    return events


# ── plan vs evidence budget (MIR-073) ────────────────────────────────────────

# A planned source keeping at most this fraction of its content after the
# total-budget trim counts as STARVED: the planner said "this file is needed",
# the allocator effectively discarded it. Measured incident: 50 of 12204
# chars (0.4%) — the self-analysis question shares no keywords with code, so
# the file the plan was built around arrived as a sliver.
_STARVED_KEEP_RATIO = 0.05


def detect_budget_starvation(
    trims: Sequence[tuple[str, int, int]],
    *,
    planned_labels: Any,
    memory_label: str | None = None,
) -> list[dict[str, Any]]:
    """Return `subsystem_disagreement` payloads for starved PLANNED sources.

    ``trims`` is what `core.evidence_budget.total_trims` parsed back out of
    the trimmed blocks: (label, kept_chars, original_chars). Pure function,
    logging-only consumer — per the operator's sensor policy this observes
    and never changes behaviour.

    The demoted memory block is exempt: memory paying first — down to the
    absolute floor — is the deliberate outcome of its own measured incident,
    not a contradiction between deciders.
    """
    events: list[dict[str, Any]] = []
    for label, kept, original in trims:
        if memory_label is not None and label == memory_label:
            continue
        if label not in planned_labels:
            continue
        if original <= 0 or kept / original > _STARVED_KEEP_RATIO:
            continue
        events.append({
            "kind": "planner_vs_evidence_budget",
            "subsystems": ["planner", "evidence_budget"],
            "severity": "medium",
            "label": label,
            "kept_chars": kept,
            "original_chars": original,
            "keep_ratio": round(kept / original, 4),
            "threshold": _STARVED_KEEP_RATIO,
            "description": (
                "Planner chose this source; the total evidence budget kept "
                f"{kept} of {original} chars — the plan's choice was "
                "effectively discarded."
            ),
        })
    return events
