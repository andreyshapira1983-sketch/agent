"""Stage A may start from the agent's own findings, not only from human TODOs.

Operator ruling 2026-08-19: «пусть Stage A берёт самоизмеренные кандидаты».
Until now its selector accepted exactly one signal class — `code_todo`, a
comment an engineer typed into the source — and ignored everything the
agent measured about itself. That was the push the charter was built to
end, still load-bearing one floor down (banked as
tests/test_stage_a_eats_only_human_pushes.py, now replaced by this file).

What Stage A may now take, and why each:
  * architecture_audit — the agent's own read-only self-analysis; the
    module that produces it calls itself "the wire that lets the agent find
    its own work from self-analysis, not only from human-authored docs".
    Its records name a real evidence file, so a failing acceptance test can
    reproduce the gap.
  * code_todo — kept, not because a human should push, but because a real
    TODO in shipped code is still a grounded defect when one exists.

What Stage A still refuses, BY NAME rather than by accident:
  * oversized_module — its target is `split:<path>`, not a file to edit,
    and its work is a module split, which has its own producer (the
    self-build splitter the charter road now feeds). Stage A's contract is
    "a defect earns a failing test"; a size limit already has a ratchet.

Measured the moment it was wired, so the record cannot drift: the door is
open and the room behind it is EMPTY. The architecture audit today reports
18/18 checks `present` and zero priority gaps, so Stage A still selects
nothing. This file pins a CONTRACT, not an achievement — nothing here says
the agent has discovered work for itself.

Gate: an audit-sourced task gets the same target gate as a verified
diagnosis — core organs open — for the identical reason recorded there:
Stage A writes ONLY a new test under tests/, edits nothing, and a human
blesses that test before any implementation exists. Path hygiene
(config/, secrets, lockfiles) still applies.
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


def test_a_human_todo_is_still_selectable() -> None:
    assert "code_todo" in _selectable_signal_sources()


def test_a_module_split_is_refused_by_name() -> None:
    """Not an oversight: splits belong to the self-build producer."""
    assert "oversized_module" not in _selectable_signal_sources()


def test_the_selector_takes_the_audit_finding(monkeypatch) -> None:
    import core.backlog_selector as bs

    backlog = [
        _candidate("oversized_module", "split:core/smart_memory.py"),
        _candidate("architecture_audit", "core/loop_gates.py"),
        _candidate("code_todo", "cli/commands_health.py"),
    ]
    monkeypatch.setattr(bs, "load_backlog", lambda ws: backlog)
    picked = _default_task_selector(".")()
    assert picked is not None
    assert picked.signal_source == "architecture_audit"


def test_backlog_order_decides_between_selectable_sources(monkeypatch) -> None:
    """No source preference of its own: the ranked backlog already decided."""
    import core.backlog_selector as bs

    backlog = [
        _candidate("code_todo", "cli/x.py"),
        _candidate("architecture_audit", "core/y.py"),
    ]
    monkeypatch.setattr(bs, "load_backlog", lambda ws: backlog)
    assert _default_task_selector(".")().signal_source == "code_todo"


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


def test_the_todo_gate_is_unchanged() -> None:
    """A human TODO does not open core organs — that stays as it was."""
    assert _target_gate_for("code_todo")("core/loop.py") is False
