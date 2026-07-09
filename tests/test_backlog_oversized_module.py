"""Tests for the oversized-module self-perception organ.

The organ flags large ``*.py`` modules as *report-only* backlog advisories: a
grounded, line-count-carrying signal whose ``target_path`` is the abstract
``split:<file>`` (no deterministic mapper → the producer names the bloated file
but never auto-rewrites it). See ``core/backlog_signals.oversized_module_candidates``.
"""
from __future__ import annotations

from pathlib import Path

from core.backlog_selector import build_backlog, load_backlog
from core.backlog_signals import (
    OVERSIZED_MODULE_SOURCE,
    _OVERSIZED_MODULE_MIN_LINES,
    _MAX_OVERSIZED_RECORDS,
    oversized_module_candidates,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _file_of(lines: int) -> str:
    return "\n".join(f"x = {i}" for i in range(lines))


# ── pure scanner ──────────────────────────────────────────────────────────────
def test_flags_file_at_or_above_threshold() -> None:
    big = _file_of(_OVERSIZED_MODULE_MIN_LINES)
    records, source = oversized_module_candidates([("core/big.py", big)])
    assert len(records) == 1
    rec = records[0]
    assert rec.signal_source == OVERSIZED_MODULE_SOURCE
    assert rec.target_path == "split:core/big.py"
    assert rec.evidence_ref == "core/big.py:1"
    assert str(_OVERSIZED_MODULE_MIN_LINES) in rec.problem_quote
    # provenance holds by construction
    assert rec.problem_quote in source


def test_ignores_file_below_threshold() -> None:
    small = _file_of(_OVERSIZED_MODULE_MIN_LINES - 1)
    records, _ = oversized_module_candidates([("core/small.py", small)])
    assert records == []


def test_sorted_worst_first() -> None:
    files = [
        ("core/a.py", _file_of(_OVERSIZED_MODULE_MIN_LINES + 10)),
        ("core/b.py", _file_of(_OVERSIZED_MODULE_MIN_LINES + 500)),
        ("core/c.py", _file_of(_OVERSIZED_MODULE_MIN_LINES + 100)),
    ]
    records, _ = oversized_module_candidates(files)
    assert [r.target_path for r in records] == [
        "split:core/b.py",
        "split:core/c.py",
        "split:core/a.py",
    ]


def test_record_cap() -> None:
    files = [
        (f"core/m{i}.py", _file_of(_OVERSIZED_MODULE_MIN_LINES + i))
        for i in range(_MAX_OVERSIZED_RECORDS + 10)
    ]
    records, _ = oversized_module_candidates(files)
    assert len(records) == _MAX_OVERSIZED_RECORDS


def test_empty_and_none_content_are_safe() -> None:
    records, source = oversized_module_candidates(
        [("", _file_of(2000)), ("core/x.py", None), ("core/y.py", "")]
    )
    assert records == []
    assert source == ""


# ── build_backlog ranking ─────────────────────────────────────────────────────
def test_oversized_ranks_below_real_work() -> None:
    big = _file_of(_OVERSIZED_MODULE_MIN_LINES + 10)
    oversized_records, oversized_text = oversized_module_candidates(
        [("core/huge.py", big)]
    )
    tech_debt = "TD-099 — Real actionable debt\nСтатус: Partial\n"
    candidates = build_backlog(
        tech_debt_text=tech_debt,
        oversized_records=oversized_records,
        oversized_text=oversized_text,
    )
    # tech-debt outranks the advisory oversized signal.
    assert candidates[0].signal_source == "tech_debt"
    assert candidates[-1].signal_source == OVERSIZED_MODULE_SOURCE


# ── integration against the real repo ─────────────────────────────────────────
def test_real_repo_surfaces_loop_as_oversized() -> None:
    candidates = load_backlog(REPO_ROOT)
    oversized = [c for c in candidates if c.signal_source == OVERSIZED_MODULE_SOURCE]
    assert oversized, "expected at least one oversized module in this repo"
    targets = {c.target_path for c in oversized}
    # core/loop.py is the known monster module; it must be reported.
    assert "split:core/loop.py" in targets
    # Worst-first: the largest module outranks its oversized peers.
    assert oversized[0].target_path == "split:core/loop.py"
