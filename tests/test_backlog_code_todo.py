"""Tests for the code self-inspection backlog signal (self-perception organ).

The agent's first "eye": it reads its own editable source, turns TODO/FIXME/XXX
comments into grounded, line-anchored backlog signals, and lets the existing
selector rank them — without ever inventing work.
"""
from __future__ import annotations

from pathlib import Path

from core.backlog_selector import build_backlog, load_backlog
from core.backlog_signals import CODE_TODO_SOURCE, code_todo_candidates


# ── code_todo_candidates (pure scanner) ───────────────────────────────────────

_SAMPLE = """\
import os


def foo():
    # TODO: rewire the cache invalidation
    return os.getpid()


def bar():
    x = 1  # FIXME handle the None case
    # XXX this branch is dead
    return x
"""


def test_scanner_finds_todo_fixme_xxx_markers():
    records, _source = code_todo_candidates([("core/foo.py", _SAMPLE)])
    quotes = {r.problem_quote for r in records}
    assert any("rewire the cache invalidation" in q for q in quotes)
    assert any("handle the None case" in q for q in quotes)
    assert any("this branch is dead" in q for q in quotes)
    assert len(records) == 3


def test_scanner_target_and_evidence_ref_point_at_source():
    records, _ = code_todo_candidates([("core/foo.py", _SAMPLE)])
    rec = next(r for r in records if "rewire" in r.problem_quote)
    assert rec.target_path == "core/foo.py"
    assert rec.evidence_ref == "core/foo.py:5"  # 1-indexed line of the TODO
    assert rec.signal_source == CODE_TODO_SOURCE


def test_scanner_quotes_are_traceable_to_source_text():
    records, source_text = code_todo_candidates([("core/foo.py", _SAMPLE)])
    # Provenance contract: every quote is an exact substring of source_text so
    # the selector's _traceable check passes by construction.
    for rec in records:
        assert rec.problem_quote in source_text


def test_scanner_ignores_lines_without_markers_and_lowercase_prose():
    text = "# just a normal comment\nx = 'todo list in a string'\n# note: fixme\n"
    records, _ = code_todo_candidates([("core/x.py", text)])
    # lowercase 'todo'/'fixme' in prose or strings must NOT match.
    assert records == []


def test_scanner_dedupes_by_file_and_line():
    records, _ = code_todo_candidates(
        [("core/a.py", "# TODO one\n"), ("core/a.py", "# TODO one\n")]
    )
    # Same file:line appears once (identical content re-fed).
    assert len(records) == 1


def test_scanner_caps_total_records():
    many = "\n".join(f"# TODO item {i}" for i in range(200)) + "\n"
    records, _ = code_todo_candidates([("core/big.py", many)])
    assert len(records) == 50  # _MAX_CODE_TODO_RECORDS


def test_scanner_truncates_overlong_quote_but_stays_traceable():
    long_line = "# TODO " + ("x" * 500)
    records, source_text = code_todo_candidates([("core/l.py", long_line + "\n")])
    assert len(records) == 1
    assert len(records[0].problem_quote) <= 200
    assert records[0].problem_quote in source_text


def test_scanner_empty_input_is_empty():
    records, source_text = code_todo_candidates([])
    assert records == []
    assert source_text == ""


# ── integration with build_backlog (ranking + provenance) ─────────────────────


def test_build_backlog_ranks_code_todo_below_tech_debt_above_anatomy():
    records, source_text = code_todo_candidates([("core/foo.py", _SAMPLE)])
    candidates = build_backlog(code_todo_records=records, code_todo_text=source_text)
    assert candidates, "code_todo records should surface as candidates"
    for cand in candidates:
        assert cand.signal_source == CODE_TODO_SOURCE
        assert cand.score == 1.1  # base score for code_todo
        assert cand.target_path == "core/foo.py"


def test_build_backlog_drops_untraceable_code_todo_record():
    # A record whose quote is NOT in the provided source_text fails provenance.
    records, _ = code_todo_candidates([("core/foo.py", _SAMPLE)])
    candidates = build_backlog(code_todo_records=records, code_todo_text="")
    assert candidates == []


# ── integration with load_backlog (real read-only file scan) ──────────────────


def test_load_backlog_scans_workspace_code_for_todos(tmp_path: Path):
    core = tmp_path / "core"
    core.mkdir()
    (core / "widget.py").write_text(
        "def go():\n    # TODO: make this real\n    return 1\n",
        encoding="utf-8",
    )
    candidates = load_backlog(tmp_path)
    code_todos = [c for c in candidates if c.signal_source == CODE_TODO_SOURCE]
    assert len(code_todos) == 1
    rec = code_todos[0]
    assert rec.target_path == "core/widget.py"
    assert rec.evidence_ref == "core/widget.py:2"


def test_load_backlog_skips_pycache(tmp_path: Path):
    core = tmp_path / "core"
    cache = core / "__pycache__"
    cache.mkdir(parents=True)
    (cache / "junk.py").write_text("# TODO ignore me\n", encoding="utf-8")
    candidates = load_backlog(tmp_path)
    assert [c for c in candidates if c.signal_source == CODE_TODO_SOURCE] == []


def test_load_backlog_empty_workspace_has_no_code_todos(tmp_path: Path):
    candidates = load_backlog(tmp_path)
    assert [c for c in candidates if c.signal_source == CODE_TODO_SOURCE] == []
