"""Tests for the grounded backlog selector (TD-036, Phase 1)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.backlog_selector import (
    BacklogCandidate,
    build_backlog,
    load_backlog,
    select_top,
)
from core.backlog_signals import SignalRecord, ValuePenalties
from core.backlog_selector import _finalize


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

_SELF_BUILD_PROPOSAL = """\
# TD-038 slice 2 - proposal: self-build grounded target coverage (docs only)

### D. Mapper coverage for a docs-only / operator-guide target **first**

- **What:** first grounded target is `docs/self_build.md` (already in
  `DEFAULT_CANDIDATE_TARGETS`).
"""


def _write_self_build_proposal(workspace: Path) -> None:
    proposal = (
        workspace
        / "docs"
        / "proposals"
        / "self-build-grounded-target-coverage-proposal.md"
    )
    proposal.parent.mkdir(parents=True)
    proposal.write_text(_SELF_BUILD_PROPOSAL, encoding="utf-8")


# ── empty / refusal ───────────────────────────────────────────────────────────


def test_empty_closed_set_yields_none():
    backlog = build_backlog(tech_debt_text="", anatomy_text="")
    assert backlog == []
    assert select_top(backlog) is None


# ── grounded candidate creation ───────────────────────────────────────────────


def test_tech_debt_open_entry_creates_grounded_candidate():
    backlog = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text="")
    targets = {c.target_path for c in backlog}
    assert {"TD-060", "TD-061"} <= targets
    top = select_top(backlog)
    assert top is not None
    assert top.problem_quote in _TECH_DEBT
    assert top.evidence_ref.startswith("TECH_DEBT.md:")
    assert top.signal_source == "tech_debt"


def test_anatomy_follow_up_creates_grounded_candidate():
    backlog = build_backlog(tech_debt_text="", anatomy_text=_ANATOMY)
    assert len(backlog) == 1
    c = backlog[0]
    assert c.signal_source == "anatomy"
    assert c.problem_quote in _ANATOMY
    assert c.target_path.startswith("anatomy:")


def test_td_038_docs_pilot_creates_grounded_docs_candidate():
    backlog = build_backlog(self_build_proposal_text=_SELF_BUILD_PROPOSAL)

    assert len(backlog) == 1
    c = backlog[0]
    assert c.signal_source == "self_build_docs"
    assert c.target_path == "docs/self_build.md"
    assert c.problem_quote in _SELF_BUILD_PROPOSAL
    assert "DEFAULT_CANDIDATE_TARGETS" in c.problem_quote
    assert c.evidence_ref.startswith(
        "docs/proposals/self-build-grounded-target-coverage-proposal.md:"
    )


def test_candidate_carries_all_required_fields():
    c = select_top(build_backlog(tech_debt_text=_TECH_DEBT))
    assert c is not None
    d = c.to_dict()
    for key in (
        "target_path", "signal_source", "evidence_ref", "problem_quote",
        "proposed_change", "proof_of_value", "expected_effect", "confidence",
        "score",
    ):
        assert key in d and d[key] != "" or key in ("score",)


# ── determinism & ranking ─────────────────────────────────────────────────────


def test_same_input_same_ranked_output():
    a = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text=_ANATOMY)
    b = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text=_ANATOMY)
    assert [c.to_dict() for c in a] == [c.to_dict() for c in b]


def test_tech_debt_outranks_anatomy():
    backlog = build_backlog(tech_debt_text=_TECH_DEBT, anatomy_text=_ANATOMY)
    assert backlog[0].signal_source == "tech_debt"  # base 2.0 > 1.0
    assert backlog[-1].signal_source == "anatomy"


def test_docs_pilot_outranks_anatomy_but_not_tech_debt():
    backlog = build_backlog(
        tech_debt_text=_TECH_DEBT,
        anatomy_text=_ANATOMY,
        self_build_proposal_text=_SELF_BUILD_PROPOSAL,
    )

    assert [c.signal_source for c in backlog] == [
        "tech_debt",
        "tech_debt",
        "self_build_docs",
        "anatomy",
    ]


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


def test_record_without_matching_source_is_dropped():
    rec = SignalRecord("tech_debt", "TD-1", "ref", "quote")
    assert _finalize([rec], {}) == []  # no source text -> untraceable -> dropped


# ── anti-repeat penalties ─────────────────────────────────────────────────────


def test_rejected_wrong_target_suppresses_candidate():
    penalties = ValuePenalties(frozenset({"TD-060"}), frozenset())
    backlog = build_backlog(tech_debt_text=_TECH_DEBT, penalties=penalties)
    targets = {c.target_path for c in backlog}
    assert "TD-060" not in targets  # suppressed
    assert "TD-061" in targets


def test_penalized_target_ranks_lower():
    penalties = ValuePenalties(frozenset(), frozenset({"TD-060"}))
    backlog = build_backlog(tech_debt_text=_TECH_DEBT, penalties=penalties)
    by_target = {c.target_path: c for c in backlog}
    assert by_target["TD-060"].score < by_target["TD-061"].score
    # penalized TD-060 should not outrank the un-penalized TD-061
    assert backlog[0].target_path == "TD-061"


# ── read-only file loading ────────────────────────────────────────────────────


def test_load_backlog_reads_workspace_files(tmp_path: Path):
    (tmp_path / "TECH_DEBT.md").write_text(_TECH_DEBT, encoding="utf-8")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "AGENT_ANATOMY.md").write_text(_ANATOMY, encoding="utf-8")
    _write_self_build_proposal(tmp_path)
    backlog = load_backlog(tmp_path)
    assert any(c.signal_source == "tech_debt" for c in backlog)
    assert any(c.signal_source == "self_build_docs" for c in backlog)
    assert any(c.signal_source == "anatomy" for c in backlog)


def test_load_backlog_docs_pilot_available_when_target_missing(tmp_path: Path):
    _write_self_build_proposal(tmp_path)

    top = select_top(load_backlog(tmp_path))

    assert top is not None
    assert top.signal_source == "self_build_docs"
    assert top.target_path == "docs/self_build.md"


def test_load_backlog_docs_pilot_not_available_after_target_exists(tmp_path: Path):
    _write_self_build_proposal(tmp_path)
    (tmp_path / "docs" / "self_build.md").write_text(
        "# Self-build\n\nAlready created.\n",
        encoding="utf-8",
    )

    backlog = load_backlog(tmp_path)

    assert not any(c.signal_source == "self_build_docs" for c in backlog)
    assert select_top(backlog) is None


def test_load_backlog_missing_files_is_empty(tmp_path: Path):
    assert load_backlog(tmp_path) == []


def test_load_backlog_applies_value_penalties(tmp_path: Path):
    (tmp_path / "TECH_DEBT.md").write_text(_TECH_DEBT, encoding="utf-8")

    class _Rev:
        item_id = "i1"
        verdict = "rejected_wrong_target"

    backlog = load_backlog(
        tmp_path, value_reviews=[_Rev()], item_target_map={"i1": "TD-060"}
    )
    assert "TD-060" not in {c.target_path for c in backlog}


def test_load_backlog_is_read_only(tmp_path: Path):
    td = tmp_path / "TECH_DEBT.md"
    td.write_text(_TECH_DEBT, encoding="utf-8")
    before = td.read_bytes()
    load_backlog(tmp_path)
    assert td.read_bytes() == before  # no writes
