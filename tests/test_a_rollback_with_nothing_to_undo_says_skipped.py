"""`:rollback` that undid nothing says so through the REAL report, not a faked summary.

The CLI test fakes `summary()` with a `skipped_reason`; the real report never had
that key, so an empty log or a mistyped plan id printed «ok=0, noop=0, error=0»
as if a rollback had run.
"""
from __future__ import annotations

from types import SimpleNamespace

from cli.commands_memory import _handle_rollback
from core import repair_commands


class _Log:
    def __init__(self):
        self.events: list = []

    def log(self, event, payload=None, **extra):
        self.events.append((event, payload))


def _agent(plans: list, log: _Log):
    def rollback(plan_id=None, *, workspace_root=None):
        return repair_commands.rollback(
            compensation_log=plans, log=log, plan_id=plan_id, workspace_root=workspace_root
        )

    return SimpleNamespace(compensation_log=plans, rollback=rollback)


def test_nothing_registered_says_skipped_and_prints_no_counts(tmp_path, capsys):
    _handle_rollback("", _agent([], _Log()), tmp_path)
    err = capsys.readouterr().err
    assert "rollback skipped: no plans registered" in err
    assert "ok=" not in err


def test_a_mistyped_plan_id_says_not_found_and_keeps_the_log(tmp_path, capsys):
    plans = [SimpleNamespace(id="cp-1")]
    _handle_rollback("cp-typo", _agent(plans, _Log()), tmp_path)
    err = capsys.readouterr().err
    assert "rollback skipped: plan_id 'cp-typo' not found" in err
    assert "ok=" not in err
    assert [p.id for p in plans] == ["cp-1"]


def test_the_journal_and_the_report_give_the_same_reason(tmp_path):
    log = _Log()
    report = repair_commands.rollback(compensation_log=[], log=log, workspace_root=tmp_path)
    event, payload = log.events[-1]
    assert event == "compensation_apply"
    assert payload["skipped_reason"] == report.summary()["skipped_reason"] == "no plans registered"
