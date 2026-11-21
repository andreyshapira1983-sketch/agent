"""`:self-apply-run` — the one command that lets approved code reach the project.

The policy itself lives in `core.self_apply_bridge`; this module wires the
runtime (inbox, VCS, test runner, kill switch, budget) and reports. At 72% the
untested part was the wiring and the reporting, which is where a mistake is
quiet rather than loud:

* it takes **exactly one** approval id — free text, extra arguments or a pasted
  patch body must be refused before the lane is touched;
* the structured log is an outcome record: a fixed key set that can never carry
  a diff or file content;
* every attempt is journalled, applied or rolled back;
* rule recording and registry loading are best-effort — neither may break the
  command, and a failure in either must not make an apply look successful.

The lane itself is faked. Running it for real is what the approval gate exists
to prevent.
"""
from __future__ import annotations

from types import SimpleNamespace

import pytest

import cli.commands_self_apply as mod
from cli.commands_self_apply import _handle_self_apply_run


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self):
        return [e for e, _ in self.events]

    def payload(self, event):
        for name, data in self.events:
            if name == event:
                return data or {}
        raise AssertionError(f"{event!r} not logged; got {self.kinds()}")


@pytest.fixture
def agent():
    return SimpleNamespace(log=FakeLog())


@pytest.fixture
def lane(monkeypatch):
    """Neutralise the runtime wiring; return the captured lane call + episodes."""
    captured: dict = {"calls": [], "episodes": []}

    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: "INBOX")
    monkeypatch.setattr(mod, "SafeVCS", lambda workspace: "VCS")
    monkeypatch.setattr(mod, "RunTestsTool", lambda workspace_root: "RUNNER")
    monkeypatch.setattr(mod, "BudgetKillSwitch", lambda path: "KILL")
    monkeypatch.setattr(mod, "default_path", lambda ws: ws / "kill.json")
    monkeypatch.setattr(mod, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        mod,
        "record_self_build_episode",
        lambda agent, *, kind, result: captured["episodes"].append({"kind": kind, "result": result}),
    )
    return captured


def use_result(monkeypatch, lane, result: dict):
    def _run(**kwargs):
        lane["calls"].append(kwargs)
        return result

    monkeypatch.setattr(mod, "run_approved_self_apply", _run)


APPLIED = {
    "proposal_id": "ap-1",
    "status": "applied",
    "reason": "tests green",
    "branch": "self-apply/ap-1",
    "files_changed": ["core/foo.py"],
    "rollback_status": "none",
    "commit_hash": "abc1234",
    "origin": "producer",
    "next_human_action": "review the branch",
}


# ── reporting ────────────────────────────────────────────────────────────────


def test_rolled_back_run_names_the_rollback_and_the_rejected_files(
    agent, tmp_path, capsys, monkeypatch, lane
):
    use_result(
        monkeypatch,
        lane,
        {
            "proposal_id": "ap-2",
            "status": "rolled_back",
            "reason": "tests failed after apply",
            "branch": "self-apply/ap-2",
            "rollback_status": "clean",
            "rejected_files": ["core/bar.py"],
            "next_human_action": "inspect the failure",
        },
    )

    assert _handle_self_apply_run("ap-2", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "status: rolled_back" in err
    assert "rollback_status: clean" in err
    assert "rejected_files: ['core/bar.py']" in err
    assert "commit:" not in err, "a rolled-back run has no commit to show"


# ── best-effort side hooks ───────────────────────────────────────────────────

def test_a_registry_that_cannot_load_becomes_none(agent, tmp_path, monkeypatch, lane):
    import core.subagent_registry as reg_mod

    def _boom(ws):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(reg_mod.SubagentRegistry, "load", staticmethod(_boom))
    use_result(monkeypatch, lane, APPLIED)

    assert _handle_self_apply_run("ap-1", agent, tmp_path) is True
    assert lane["calls"][0]["registry"] is None


def test_failure_rules_are_recorded_and_logged(agent, tmp_path, monkeypatch, lane):
    """A rollback teaches a durable rule; the command records that it did."""
    import core.self_build_rules as rules_mod

    seen: dict = {}

    def _record(workspace, result):
        seen["result"] = result
        return ["no ImportError from core.y"]

    monkeypatch.setattr(rules_mod, "record_rules_from_result", _record)
    use_result(monkeypatch, lane, {**APPLIED, "status": "rolled_back"})

    assert _handle_self_apply_run("ap-1", agent, tmp_path) is True

    assert seen["result"]["status"] == "rolled_back"
    logged = agent.log.payload("self_build_rules_recorded")
    # `lessons_added` появилось 2026-09-17 вместе с уроком о происхождении:
    # откат теперь учит дважды — узкому правилу для Критика и уроку, который
    # читает автономный путь применения перед повтором по тому же адресу.
    assert logged == {
        "proposal_id": "ap-1",
        "rules_added": ["no ImportError from core.y"],
        "lessons_added": 1,
    }


def test_rule_recording_failure_never_breaks_the_command(
    agent, tmp_path, capsys, monkeypatch, lane
):
    import core.self_build_rules as rules_mod

    def _boom(ws, result):
        raise RuntimeError("rules file corrupt")

    monkeypatch.setattr(rules_mod, "record_rules_from_result", _boom)
    use_result(monkeypatch, lane, APPLIED)

    assert _handle_self_apply_run("ap-1", agent, tmp_path) is True

    assert "status: applied" in capsys.readouterr().err
    assert "self_apply_run" in agent.log.kinds()
    assert "self_build_rules_recorded" not in agent.log.kinds()
