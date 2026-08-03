"""Tests for the read-only backlog signal parsers (TD-036, Phase 1)."""
from __future__ import annotations

from core.backlog_signals import (
    ValuePenalties,
    anatomy_candidates,
    open_tech_debt,
    value_review_penalties,
)


# ── TECH_DEBT parsing ─────────────────────────────────────────────────────────

_TECH_DEBT = """\
Intro line, no status.

======
TD-050 — Done Thing
Статус: Done

Проблема
- already shipped.

======
TD-051 — Open Thing
Статус: Partial — foundation done, apply deferred.

Проблема
- the write path is not wired.

======
TD-052 — Deferred Thing
Статус:
- TD-052 something: Partial — отложено.

======
TD-053 — Fully Done
Статус: Done (read-only)
"""


def test_open_tech_debt_selects_only_non_done():
    records = open_tech_debt(_TECH_DEBT)
    targets = {r.target_path for r in records}
    assert "TD-051" in targets  # Partial
    assert "TD-052" in targets  # empty/deferred status
    assert "TD-050" not in targets  # Done
    assert "TD-053" not in targets  # Done (read-only)


def test_open_tech_debt_quote_is_traceable_and_ref_points_at_source():
    records = open_tech_debt(_TECH_DEBT)
    rec = next(r for r in records if r.target_path == "TD-051")
    assert rec.problem_quote in _TECH_DEBT  # exact substring
    assert rec.problem_quote == "TD-051 — Open Thing"
    assert rec.evidence_ref.startswith("TECH_DEBT.md:")
    assert rec.signal_source == "tech_debt"


def test_open_tech_debt_empty_input_is_empty():
    assert open_tech_debt("") == []


def test_open_tech_debt_multi_id_title_is_supported():
    text = "TD-011 / TD-012 — Combo\nСтатус: Partial — half done.\n"
    records = open_tech_debt(text)
    assert len(records) == 1
    assert records[0].target_path == "TD-011 / TD-012"


# ── AGENT_ANATOMY parsing ─────────────────────────────────────────────────────

_ANATOMY = """\
# Some Section

Text that is not a candidate.

## Candidate follow-ups (TD-030+) — advisory only

1. **TD-030 (candidate): Unify the four mechanisms.** Converge them.
2. **TD-031 (candidate): Close the ledger loop.** Wire outcomes.

## Next Section

1. **Not a candidate.** ignore me.
"""


def test_anatomy_candidates_parses_bold_headings_only_in_section():
    records = anatomy_candidates(_ANATOMY)
    quotes = [r.problem_quote for r in records]
    assert "TD-030 (candidate): Unify the four mechanisms." in quotes
    assert "TD-031 (candidate): Close the ledger loop." in quotes
    assert "Not a candidate." not in quotes  # outside the advisory section


def test_anatomy_candidates_quote_traceable_and_targets_namespaced():
    records = anatomy_candidates(_ANATOMY)
    for r in records:
        assert r.problem_quote in _ANATOMY
        assert r.target_path.startswith("anatomy:")
        assert r.signal_source == "anatomy"
        assert r.evidence_ref.startswith("docs/AGENT_ANATOMY.md:")


def test_anatomy_candidates_missing_section_is_empty():
    assert anatomy_candidates("# no candidates here\n\ntext") == []


# ── value-review penalties (anti-repeat) ──────────────────────────────────────


class _Rev:
    def __init__(self, item_id: str, verdict: str) -> None:
        self.item_id = item_id
        self.verdict = verdict


def test_value_review_penalties_need_a_target_map():
    reviews = [_Rev("i1", "rejected_wrong_target")]
    # No map -> best-effort no-op, nothing suppressed.
    assert value_review_penalties(reviews, None) == ValuePenalties.empty()


def test_value_review_penalties_suppress_and_penalize():
    reviews = [
        _Rev("i1", "rejected_wrong_target"),
        _Rev("i2", "rejected_low_value"),
        _Rev("i3", "accepted"),
    ]
    mapping = {"i1": "core/a.py", "i2": "core/b.py", "i3": "core/c.py"}
    pen = value_review_penalties(reviews, mapping)
    assert pen.suppressed == frozenset({"core/a.py"})
    assert pen.penalized == frozenset({"core/b.py"})
    assert "core/c.py" not in pen.suppressed | pen.penalized


def test_value_review_penalties_latest_verdict_wins():
    reviews = [
        _Rev("i1", "rejected_wrong_target"),
        _Rev("i1", "accepted"),  # later -> clears suppression
    ]
    pen = value_review_penalties(reviews, {"i1": "core/a.py"})
    assert pen.suppressed == frozenset()
    assert pen.penalized == frozenset()


def test_value_review_penalties_suppressed_not_also_penalized():
    reviews = [_Rev("i1", "rejected_wrong_target")]
    pen = value_review_penalties(reviews, {"i1": "t"})
    assert "t" in pen.suppressed and "t" not in pen.penalized
