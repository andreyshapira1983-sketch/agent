"""Stage A grounds itself in the agent's own findings — and ONLY those.

Two operator rulings, in order:
  * 2026-08-19 «пусть Stage A берёт самоизмеренные кандидаты» — the selector
    opened beyond `code_todo` to `architecture_audit` (this file's first form).
  * 2026-08-28 «убрать TODO/FIXME — это старая модель, стереть» — `code_todo`
    itself was erased everywhere: a marker a person typed into the source is
    the retired "human assigns — agent executes" push model, not a
    self-measured signal. The erasure superseded the 08-19 «kept» clause;
    history in MIR-183 (docs/audit/MASTER_ISSUE_REGISTRY.md).

What Stage A may take now, and why:
  * architecture_audit — the agent's own read-only self-analysis; its records
    name a real evidence file, so a failing acceptance test can reproduce the
    gap.

What it refuses, BY NAME rather than by accident:
  * oversized_module — its target is `split:<path>`, not a file to edit, and
    its work belongs to the self-build splitter.
  * code_todo — erased; the source no longer exists in the backlog, and a
    stray record wearing the old name must not be selectable either.

Gate: an audit-sourced task gets the same target gate as a verified
diagnosis — core organs open — because Stage A writes ONLY a new test under
tests/, edits nothing, and a human blesses that test before any
implementation exists. Path hygiene (config/, secrets, lockfiles) applies.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.self_task_producer import (
    _default_task_selector,
    _selectable_signal_sources,
    _target_gate_for,
)


def _candidate(source: str, target: str):
    return SimpleNamespace(
        signal_source=source,
        target_path=target,
        problem_quote=f"{source} finding about {target}",
        evidence_ref=f"{target}:1",
    )


def test_an_audit_finding_is_selectable() -> None:
    assert "architecture_audit" in _selectable_signal_sources()


def test_a_human_todo_is_no_longer_selectable() -> None:
    """The 2026-08-28 erasure: the push model does not re-enter by name."""
    assert "code_todo" not in _selectable_signal_sources()


def test_a_module_split_is_refused_by_name() -> None:
    """Not an oversight: splits belong to the self-build producer."""
    assert "oversized_module" not in _selectable_signal_sources()


def test_the_selector_takes_the_audit_finding(monkeypatch) -> None:
    import core.backlog_selector as bs

    backlog = [
        _candidate("oversized_module", "split:core/smart_memory.py"),
        _candidate("architecture_audit", "core/loop_gates.py"),
    ]
    monkeypatch.setattr(bs, "load_backlog", lambda ws: backlog)
    picked = _default_task_selector(".")()
    assert picked is not None
    assert picked.signal_source == "architecture_audit"


def test_a_stray_todo_record_is_skipped_not_selected(monkeypatch) -> None:
    """A record wearing the erased name (e.g. from an old cached backlog)
    must be walked past, exactly like a split."""
    import core.backlog_selector as bs

    backlog = [
        _candidate("code_todo", "cli/x.py"),
        _candidate("architecture_audit", "core/y.py"),
    ]
    monkeypatch.setattr(bs, "load_backlog", lambda ws: backlog)
    assert _default_task_selector(".")().signal_source == "architecture_audit"


def test_a_backlog_of_only_splits_yields_nothing(monkeypatch) -> None:
    import core.backlog_selector as bs

    monkeypatch.setattr(bs, "load_backlog", lambda ws: [
        _candidate("oversized_module", "split:core/smart_memory.py"),
    ])
    assert _default_task_selector(".")() is None


def test_an_audit_target_in_core_passes_its_gate() -> None:
    """The whole point: audit findings live in the organs. Stage A writes a
    test, never an edit, so the diagnosis-grade gate applies."""
    assert _target_gate_for("architecture_audit")("core/loop.py") is True


def test_path_hygiene_still_closes_config_for_audits() -> None:
    gate = _target_gate_for("architecture_audit")
    assert gate("config/credentials.json") is False


def test_an_unknown_source_gets_the_conservative_gate() -> None:
    """A source nobody vouched for does not open core organs."""
    assert _target_gate_for("somebody_new")("core/loop.py") is False
