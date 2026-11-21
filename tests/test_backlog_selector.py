"""Tests for the grounded backlog selector (TD-036, Phase 1)."""
from __future__ import annotations

from core.backlog_selector import (
    _finalize,
    build_backlog,
    select_top,
)
from core.backlog_signals import SignalRecord, ValuePenalties

_TECH_DEBT = """\
TD-060 — Open A
Статус: Partial — not finished.

TD-061 — Open B
Статус: Partial — also not finished.
"""

_ANATOMY = """\
## Candidate follow-ups (TD-030+) — advisory only

1. **TD-030 (candidate): Do the thing.** rationale.
"""


# ── grounded candidate creation ───────────────────────────────────────────────


def test_candidate_carries_all_required_fields():
    c = select_top(build_backlog(tech_debt_text=_TECH_DEBT))
    assert c is not None
    d = c.to_dict()
    for key in (
        "target_path", "signal_source", "evidence_ref", "problem_quote",
        "proposed_change", "proof_of_value", "expected_effect", "confidence",
        "score",
    ):
        assert (key in d and d[key] != "") or key == "score"


# ── determinism & ranking ─────────────────────────────────────────────────────


def test_same_input_same_ranked_output():
    a = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text=_ANATOMY)
    b = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text=_ANATOMY)
    assert [c.to_dict() for c in a] == [c.to_dict() for c in b]


# ── provenance validation ─────────────────────────────────────────────────────


def test_untraceable_quote_is_dropped():
    # A record whose quote is NOT a substring of the cited source is dropped.
    bogus = SignalRecord(
        signal_source="tech_debt",
        target_path="TD-999",
        evidence_ref="TECH_DEBT.md:1",
        problem_quote="a fabricated problem never in the file",
    )
    good = SignalRecord(
        signal_source="tech_debt",
        target_path="TD-060",
        evidence_ref="TECH_DEBT.md:1",
        problem_quote="TD-060 — Open A",
    )
    out = _finalize([bogus, good], {"tech_debt": _TECH_DEBT})
    targets = {c.target_path for c in out}
    assert "TD-060" in targets
    assert "TD-999" not in targets


# ── anti-repeat penalties ─────────────────────────────────────────────────────


def test_rejected_wrong_target_suppresses_candidate():
    penalties = ValuePenalties(frozenset({"TD-060"}), frozenset())
    backlog = build_backlog(tech_debt_text=_TECH_DEBT, penalties=penalties)
    targets = {c.target_path for c in backlog}
    assert "TD-060" not in targets  # suppressed
    assert "TD-061" in targets
