"""`:approval-*` and `:ack*` — the REPL side of "dangerous actions need a human".

These handlers decide whether an approved item may actually run, and whether an
alert may be silenced. Both are safety decisions, and both were largely
unexercised (the module sat at 54%).

What is pinned here, above the line counts:

* an item runs **only** when its status is `approved` and its operation is one
  the runner supports — every other shape is refused before any runtime object
  is constructed;
* an item is marked `executed` **only** on a completed run / successful
  subagent — a refusal, an error, or a non-completed report must leave the
  inbox entry alone, so nothing looks done that is not;
* only advisory alerts can be acknowledged: objective breakages (daemon, tests,
  tick errors) can never be suppressed;
* ledger hooks are best-effort — a registry failure must never break or fake an
  approval decision.

Everything runs against a real `ApprovalInbox` in `tmp_path`. The autonomous
runtime and the subagent runner are the only fakes, because running them for
real is exactly what these gates exist to prevent.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.commands_approval as mod
from cli.commands_approval import (
    _alert_ack_store_for,
    _approval_inbox_for,
    _handle_alert_ack,
    _handle_alert_ack_clear,
    _handle_alert_ack_list,
    _handle_approval_abort,
    _handle_approval_decision,
    _handle_approval_list,
    _handle_approval_run,
    _handle_approval_triage,
    _handle_best_next_action,
    _handle_self_issue_verify,
    _payload_bool,
    _record_producer_approval,
    _record_subagent_contract_outcome,
)


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, object]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self) -> list[str]:
        return [e for e, _ in self.events]


def make_agent(**extra) -> SimpleNamespace:
    return SimpleNamespace(log=FakeLog(), **extra)


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "data").mkdir(parents=True, exist_ok=True)
    return tmp_path


@pytest.fixture
def agent():
    return make_agent()


def add_item(agent, workspace: Path, **kwargs):
    inbox = _approval_inbox_for(agent, workspace)
    defaults = {"operation": "autonomous_runtime.allow_effects", "summary": "s"}
    return inbox.add(**{**defaults, **kwargs})


# ── _payload_bool: the parser that guards runtime config ─────────────────────

@pytest.mark.parametrize(
    "value,default,expected",
    [
        (None, True, True),
        (None, False, False),
        (True, False, True),
        (False, True, False),
        ("yes", False, True),
        ("ON", False, True),
        ("1", False, True),
        ("no", True, False),
        (" Off ", True, False),
        ("0", True, False),
    ],
)
def test_payload_bool_accepts_the_documented_spellings(value, default, expected):
    assert _payload_bool(value, default=default) is expected


@pytest.mark.parametrize("value", ["maybe", "", 2, [], "truthy"])
def test_payload_bool_refuses_anything_else(value):
    """A junk flag must raise, not silently become True."""
    with pytest.raises(ValueError):
        _payload_bool(value, default=True)


# ── :approval-run — the gate ─────────────────────────────────────────────────

def test_approval_run_without_an_id_prints_usage(agent, workspace, capsys):
    assert _handle_approval_run("", agent, workspace) is True
    assert "Usage: :approval-run" in capsys.readouterr().err


def test_approval_run_reports_an_unknown_id(agent, workspace, capsys):
    assert _handle_approval_run("nope-123", agent, workspace) is True
    assert "approval not found: nope-123" in capsys.readouterr().err


def test_pending_item_is_refused_and_no_runtime_is_built(agent, workspace, capsys, monkeypatch):
    item = add_item(agent, workspace)  # status: pending
    monkeypatch.setattr(
        mod, "AutonomousRuntime", lambda *a, **k: pytest.fail("runtime must not be constructed")
    )

    assert _handle_approval_run(item.id, agent, workspace) is True

    err = capsys.readouterr().err
    assert "refused" in err and "status=pending" in err
    assert "approve it first" in err


def test_unsupported_operation_is_refused_after_approval(agent, workspace, capsys, monkeypatch):
    item = add_item(agent, workspace, operation="rm -rf /")
    _approval_inbox_for(agent, workspace).approve(item.id)
    monkeypatch.setattr(
        mod, "AutonomousRuntime", lambda *a, **k: pytest.fail("runtime must not be constructed")
    )

    assert _handle_approval_run(item.id, agent, workspace) is True
    assert "unsupported operation=rm -rf /" in capsys.readouterr().err


def test_invalid_payload_stops_before_the_runtime(agent, workspace, capsys, monkeypatch):
    item = add_item(agent, workspace, payload={"include_tests": "maybe"})
    _approval_inbox_for(agent, workspace).approve(item.id)
    monkeypatch.setattr(
        mod, "AutonomousRuntime", lambda *a, **k: pytest.fail("runtime must not be constructed")
    )

    assert _handle_approval_run(item.id, agent, workspace) is True
    assert "invalid payload" in capsys.readouterr().err


class FakeRuntime:
    """Records the config it was handed and returns a scripted report."""

    last_config = None

    def __init__(self, agent, *, workspace, approval_inbox):
        self.inbox = approval_inbox

    def __call__(self, *a, **k):  # pragma: no cover - guard
        raise AssertionError("unused")

    def run(self, config):
        FakeRuntime.last_config = config
        return SimpleNamespace(status=self.status, user_summary=lambda: f"RUN-{self.status}")


def _runtime_factory(status: str):
    def _make(agent, *, workspace, approval_inbox):
        rt = FakeRuntime(agent, workspace=workspace, approval_inbox=approval_inbox)
        rt.status = status
        return rt
    return _make


def test_completed_run_marks_the_item_executed(agent, workspace, capsys, monkeypatch):
    item = add_item(agent, workspace, payload={"goal": "tidy", "limit": 2, "include_tests": "no"})
    inbox = _approval_inbox_for(agent, workspace)
    inbox.approve(item.id)
    monkeypatch.setattr(mod, "AutonomousRuntime", _runtime_factory("completed"))

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert inbox.get(item.id).status == "executed"
    assert "approval_inbox_executed" in agent.log.kinds()
    assert "RUN-completed" in capsys.readouterr().err
    cfg = FakeRuntime.last_config
    assert cfg.goal == "tidy" and cfg.limit == 2
    assert cfg.include_tests is False, "payload flags must reach the runtime config"
    assert cfg.dry_run is False and cfg.effects_approved is True


def test_unfinished_run_leaves_the_item_unexecuted(agent, workspace, capsys, monkeypatch):
    """Nothing may look done that did not finish."""
    item = add_item(agent, workspace)
    inbox = _approval_inbox_for(agent, workspace)
    inbox.approve(item.id)
    monkeypatch.setattr(mod, "AutonomousRuntime", _runtime_factory("stopped"))

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert inbox.get(item.id).status == "approved"
    assert "approval_inbox_executed" not in agent.log.kinds()
    assert "RUN-stopped" in capsys.readouterr().err


# ── approve / deny / abort ───────────────────────────────────────────────────

@pytest.mark.parametrize("decision", ["approve", "deny"])
def test_decision_without_an_id_prints_usage(agent, workspace, capsys, decision):
    assert _handle_approval_decision("", agent, workspace, decision=decision) is True
    assert f"Usage: :approval-{decision}" in capsys.readouterr().err


@pytest.mark.parametrize("decision,past", [("approve", "approved"), ("deny", "denied")])
def test_decision_updates_the_item_and_logs_it(agent, workspace, capsys, decision, past):
    item = add_item(agent, workspace)
    assert _handle_approval_decision(item.id, agent, workspace, decision=decision) is True

    assert _approval_inbox_for(agent, workspace).get(item.id).status == past
    assert "approval_inbox_decision" in agent.log.kinds()
    assert f"approval {past}: {item.id}" in capsys.readouterr().err


def test_unknown_id_is_reported_not_raised(agent, workspace, capsys):
    assert _handle_approval_decision("ghost", agent, workspace, decision="approve") is True
    assert "approval approve failed" in capsys.readouterr().err


def test_unknown_decision_word_is_a_programming_error(agent, workspace):
    """Only KeyError is caught; a bad decision string must surface."""
    item = add_item(agent, workspace)
    with pytest.raises(ValueError, match="unknown approval decision"):
        _handle_approval_decision(item.id, agent, workspace, decision="maybe")


def test_abort_usage_unknown_and_success(agent, workspace, capsys):
    assert _handle_approval_abort("", agent, workspace) is True
    assert "Usage: :approval-abort" in capsys.readouterr().err

    assert _handle_approval_abort("ghost", agent, workspace) is True
    assert "approval abort failed" in capsys.readouterr().err

    item = add_item(agent, workspace)
    assert _handle_approval_abort(item.id, agent, workspace) is True
    assert _approval_inbox_for(agent, workspace).get(item.id).status == "aborted"
    assert f"approval aborted: {item.id}" in capsys.readouterr().err


def test_producer_ledger_failure_never_breaks_an_approval(agent, workspace, monkeypatch):
    """The TD-031 hook is best-effort by contract."""
    from core.self_build_producer import PRODUCER_ORIGIN

    item = add_item(
        agent,
        workspace,
        operation="self_apply_lane.run",
        payload={"origin": PRODUCER_ORIGIN},
    )
    import core.subagent_registry as reg_mod

    def _boom(*a, **k):
        raise RuntimeError("registry on fire")

    monkeypatch.setattr(reg_mod.SubagentRegistry, "load", staticmethod(_boom))

    assert _handle_approval_decision(item.id, agent, workspace, decision="approve") is True
    assert _approval_inbox_for(agent, workspace).get(item.id).status == "approved"


def test_producer_ledger_ignores_items_from_other_origins(workspace):
    """A non-producer item must not reach the registry at all."""
    _record_producer_approval(workspace, SimpleNamespace(operation="something_else"))
    _record_producer_approval(
        workspace,
        SimpleNamespace(operation="self_apply_lane.run", payload={"origin": "human"}),
    )
    _record_producer_approval(
        workspace, SimpleNamespace(operation="self_apply_lane.run", payload=None)
    )


# ── :approval-list / :approval-triage ────────────────────────────────────────

def test_list_reports_emptiness_and_then_content(agent, workspace, capsys):
    assert _handle_approval_list("", agent, workspace) is True
    assert "(no approvals: status=pending)" in capsys.readouterr().err

    item = add_item(agent, workspace, reasons=("because",), risk="irreversible")
    bare = add_item(agent, workspace, summary="no reasons given")
    assert _handle_approval_list("pending", agent, workspace) is True

    err = capsys.readouterr().err
    assert "=== approval inbox (2; status=pending) ===" in err
    assert item.id in err and "risk=irreversible" in err
    assert "reasons=['because']" in err
    assert bare.id in err, "an item without reasons is still listed"
    assert err.count("reasons=") == 1, "the reasons line is printed only when there are reasons"


def test_triage_is_read_only_and_logs_its_report(agent, workspace, capsys):
    add_item(agent, workspace, operation="proposed_task", summary="do a thing")
    assert _handle_approval_triage("", agent, workspace) is True

    assert "approval_inbox_triage" in agent.log.kinds()
    assert capsys.readouterr().err.strip(), "triage must print a report"
    assert _approval_inbox_for(agent, workspace).pending(), "triage must not drain the inbox"


# ── :ack / :ack-list / :ack-clear ────────────────────────────────────────────

def test_ack_without_an_action_prints_usage(agent, workspace, capsys):
    assert _handle_alert_ack("", agent, workspace) is True
    assert "Usage: :ack <action>" in capsys.readouterr().err


@pytest.mark.parametrize(
    "action", ["daemon_down", "fix_failing_tests", "investigate_tick_error", "anything_else"]
)
def test_objective_breakages_can_never_be_acknowledged(agent, workspace, capsys, action):
    assert _handle_alert_ack(action, agent, workspace) is True

    err = capsys.readouterr().err
    assert "ack refused" in err
    assert "can never be suppressed" in err
    assert _alert_ack_store_for(workspace).list_active() == []
    assert "alert_acknowledged" not in agent.log.kinds()


def test_advisory_alert_is_acknowledged_with_ttl_and_reason(agent, workspace, capsys):
    assert _handle_alert_ack(
        "review_inbox_backlog --ttl 3 backlog is known", agent, workspace
    ) is True

    err = capsys.readouterr().err
    assert "acknowledged: review_inbox_backlog" in err
    assert "expires" in err
    assert "backlog is known" in err
    assert "alert_acknowledged" in agent.log.kinds()

    active = _alert_ack_store_for(workspace).list_active()
    assert [a.action for a in active] == ["review_inbox_backlog"]
    assert active[0].reason == "backlog is known"
    assert active[0].expires_at


def test_invalid_ttl_is_ignored_but_the_ack_still_happens(agent, workspace, capsys):
    assert _handle_alert_ack("review_dry_run_stall --ttl soon", agent, workspace) is True

    err = capsys.readouterr().err
    assert "invalid --ttl value 'soon', ignoring" in err
    assert "(no expiry)" in err
    assert _alert_ack_store_for(workspace).list_active()[0].expires_at is None


def test_ack_list_empty_then_populated(agent, workspace, capsys):
    assert _handle_alert_ack_list("", agent, workspace) is True
    assert "no active acknowledgements" in capsys.readouterr().err

    _handle_alert_ack("review_dry_run_stall waiting on CI", agent, workspace)
    capsys.readouterr()

    assert _handle_alert_ack_list("", agent, workspace) is True
    err = capsys.readouterr().err
    assert "active acknowledgement(s): 1" in err
    assert "review_dry_run_stall" in err and "no expiry" in err
    assert "waiting on CI" in err


def test_ack_clear_usage_miss_and_hit(agent, workspace, capsys):
    assert _handle_alert_ack_clear("", agent, workspace) is True
    assert "Usage: :ack-clear" in capsys.readouterr().err

    assert _handle_alert_ack_clear("review_inbox_backlog", agent, workspace) is True
    assert "no active acknowledgement found" in capsys.readouterr().err

    _handle_alert_ack("review_inbox_backlog", agent, workspace)
    capsys.readouterr()
    assert _handle_alert_ack_clear("review_inbox_backlog", agent, workspace) is True

    assert "cleared acknowledgement for: review_inbox_backlog" in capsys.readouterr().err
    assert "alert_ack_cleared" in agent.log.kinds()
    assert _alert_ack_store_for(workspace).list_active() == []


# ── :best-next-action ────────────────────────────────────────────────────────

def test_best_next_action_runs_on_a_cold_workspace(agent, workspace, capsys):
    """No heartbeat, no inbox, no registry — it must still advise, not crash."""
    assert _handle_best_next_action("", agent, workspace) is True

    assert "best_next_action" in agent.log.kinds()
    assert capsys.readouterr().err.strip()


def test_best_next_action_json_mode_emits_parsable_json(agent, workspace, capsys):
    assert _handle_best_next_action("--json", agent, workspace) is True
    payload = json.loads(capsys.readouterr().err)
    assert "action" in payload


def test_acknowledged_alerts_are_named_in_the_report(agent, workspace, capsys):
    _handle_alert_ack("review_inbox_backlog", agent, workspace)
    capsys.readouterr()

    assert _handle_best_next_action("", agent, workspace) is True
    assert "acknowledged alert(s) currently suppressed: review_inbox_backlog" in (
        capsys.readouterr().err
    )


# ── :self-issue-verify ───────────────────────────────────────────────────────

def _registry(workspace: Path):
    from core.self_improvement_issues import (
        DEFAULT_ISSUE_PATH,
        SelfImprovementIssueRegistry,
    )

    return SelfImprovementIssueRegistry(workspace / DEFAULT_ISSUE_PATH)


@pytest.mark.parametrize("rest", ["", "a b"])
def test_self_issue_verify_needs_exactly_one_fingerprint(agent, workspace, capsys, rest):
    assert _handle_self_issue_verify(rest, agent, workspace) is True
    assert "Usage: :self-issue-verify" in capsys.readouterr().err


def test_unknown_fingerprint_is_refused(agent, workspace, capsys):
    assert _handle_self_issue_verify("nosuch", agent, workspace) is True
    assert "unknown fingerprint nosuch" in capsys.readouterr().err


def test_issue_without_a_targeted_verifier_is_refused(agent, workspace, capsys):
    """Only actions with a registered regression test may be auto-verified."""
    reg = _registry(workspace)
    issue = reg.upsert_failure("some unrelated failure text", "2020-01-01T00:00:00+00:00")
    assert issue.action not in mod._SELF_ISSUE_VERIFIERS

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True
    assert f"no targeted verifier for action {issue.action}" in capsys.readouterr().err


def test_already_resolved_issue_is_not_verified_again(agent, workspace, capsys):
    reg = _registry(workspace)
    issue = reg.upsert_failure("another failure text", "2020-01-01T00:00:00+00:00")
    reg.transition(
        status="resolved",
        observed_at="2020-01-02T00:00:00+00:00",
        fingerprint=issue.fingerprint,
        action=issue.action,
        evidence="fixed earlier",
    )

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True
    assert f"already resolved: {issue.fingerprint}" in capsys.readouterr().err


# ── ledger hooks are best-effort ─────────────────────────────────────────────

def test_contract_ledger_failure_is_reported_but_swallowed(workspace, capsys, monkeypatch):
    import core.subagent_registry as reg_mod

    def _boom(*a, **k):
        raise RuntimeError("disk full")

    monkeypatch.setattr(reg_mod.SubagentRegistry, "load", staticmethod(_boom))

    _record_subagent_contract_outcome(workspace, SimpleNamespace(contract_id="c1"), "executed")

    assert "contract registry write failed: RuntimeError: disk full" in capsys.readouterr().err


# ── the approved-subagent path ───────────────────────────────────────────────
#
# Every failure shape here must leave the inbox item NOT executed: a refusal or
# a crash that still marked the approval as done would make an unrun action
# look completed.

class FakeSpawnTool:
    def __init__(self, result=None, error: Exception | None = None, with_runner: bool = True):
        self._result = result
        self._error = error
        self.calls: list[tuple] = []
        if not with_runner:
            self.run_contract = None

    def run_contract(self, contract, *, approved):
        self.calls.append((contract, approved))
        if self._error is not None:
            raise self._error
        return self._result


def _approved_subagent_item(agent, workspace):
    inbox = _approval_inbox_for(agent, workspace)
    item = inbox.add(operation="launch_subagent", summary="run a subagent", payload={"x": 1})
    inbox.approve(item.id)
    return inbox, item


@pytest.fixture
def contract_stub(monkeypatch):
    contract = SimpleNamespace(contract_id="c-1")
    monkeypatch.setattr(mod, "canonical_from_approval_payload", lambda payload: contract)
    return contract


@pytest.fixture
def recorded_outcomes(monkeypatch):
    """Capture the ledger hook instead of writing a real registry."""
    seen: list[str] = []
    monkeypatch.setattr(
        mod,
        "_record_subagent_contract_outcome",
        lambda ws, contract, outcome, **kw: seen.append(outcome),
    )
    return seen


def test_invalid_subagent_payload_is_refused(agent, workspace, capsys, monkeypatch):
    inbox, item = _approved_subagent_item(agent, workspace)

    def _bad(payload):
        raise ValueError("payload is not a contract")

    monkeypatch.setattr(mod, "canonical_from_approval_payload", _bad)

    assert _handle_approval_run(item.id, agent, workspace) is True
    assert "invalid subagent payload" in capsys.readouterr().err
    assert inbox.get(item.id).status == "approved"


def test_missing_spawn_tool_is_reported(agent, workspace, capsys, contract_stub):
    def _raise(name):
        raise KeyError(name)

    agent.registry = SimpleNamespace(get=_raise)
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True
    assert "approval run failed" in capsys.readouterr().err
    assert inbox.get(item.id).status == "approved"


def test_spawn_tool_without_canonical_runner_is_refused(agent, workspace, capsys, contract_stub):
    agent.registry = SimpleNamespace(get=lambda name: FakeSpawnTool(with_runner=False))
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True
    assert "lacks canonical runner support" in capsys.readouterr().err
    assert inbox.get(item.id).status == "approved"


def test_contract_refusal_is_recorded_and_not_executed(
    agent, workspace, capsys, contract_stub, recorded_outcomes
):
    from core.subagent_runner import SubagentContractRefused

    agent.registry = SimpleNamespace(
        get=lambda name: FakeSpawnTool(error=SubagentContractRefused("scope too wide"))
    )
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert recorded_outcomes == ["refused"]
    assert "approval run refused: scope too wide" in capsys.readouterr().err
    assert "approval_subagent_refused" in agent.log.kinds()
    assert inbox.get(item.id).status == "approved", "a refused contract is not executed"


def test_runner_crash_is_recorded_and_not_executed(
    agent, workspace, capsys, contract_stub, recorded_outcomes
):
    agent.registry = SimpleNamespace(get=lambda name: FakeSpawnTool(error=RuntimeError("boom")))
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert recorded_outcomes == ["error"]
    assert "approval run failed: RuntimeError: boom" in capsys.readouterr().err
    assert "approval_subagent_error" in agent.log.kinds()
    assert inbox.get(item.id).status == "approved"


def test_unsuccessful_result_is_recorded_and_not_executed(
    agent, workspace, capsys, contract_stub, recorded_outcomes
):
    result = SimpleNamespace(
        status="error", error="tool budget exceeded", execution_receipt=None, contract_audit=None
    )
    agent.registry = SimpleNamespace(get=lambda name: FakeSpawnTool(result=result))
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert recorded_outcomes == ["error"]
    assert "approval run failed: tool budget exceeded" in capsys.readouterr().err
    assert inbox.get(item.id).status == "approved"


def test_successful_subagent_is_recorded_and_marked_executed(
    agent, workspace, capsys, contract_stub, recorded_outcomes
):
    result = SimpleNamespace(
        status="success",
        execution_receipt={"r": 1},
        contract_audit={"a": 1},
        to_evidence_text=lambda: "SUBAGENT-EVIDENCE",
    )
    tool = FakeSpawnTool(result=result)
    agent.registry = SimpleNamespace(get=lambda name: tool)
    inbox, item = _approved_subagent_item(agent, workspace)

    assert _handle_approval_run(item.id, agent, workspace) is True

    assert recorded_outcomes == ["executed"]
    assert tool.calls[0][1] is True, "the runner is told the contract was approved"
    assert inbox.get(item.id).status == "executed"
    assert "approval_inbox_executed" in agent.log.kinds()
    assert "SUBAGENT-EVIDENCE" in capsys.readouterr().err


# ── :self-issue-verify, the execution half ───────────────────────────────────

class FakeRunner:
    """Stands in for RunTestsTool: constructed, then `run` + `validate_output`."""

    def __init__(self, *, result=None, error: Exception | None = None, valid: bool = True):
        self._result = result or {}
        self._error = error
        self._valid = valid

    def __call__(self, *, workspace_root):
        return self

    def run(self, *, paths, pattern):
        if self._error is not None:
            raise self._error
        return self._result

    def validate_output(self, result):
        return self._valid, ()


def _splitter_issue(workspace):
    """An issue whose action HAS a registered verifier."""
    reg = _registry(workspace)
    issue = reg.upsert_failure(
        "duplicate mixin base class in core/incremental_splitter.py",
        "2020-01-01T00:00:00+00:00",
    )
    assert issue.action in mod._SELF_ISSUE_VERIFIERS, "fixture must hit the verifier table"
    return reg, issue


def test_verifier_infrastructure_error_leaves_the_issue_open(agent, workspace, capsys, monkeypatch):
    import tools.run_tests as run_tests_mod

    _reg, issue = _splitter_issue(workspace)
    monkeypatch.setattr(run_tests_mod, "RunTestsTool", FakeRunner(error=OSError("no python")))

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True

    err = capsys.readouterr().err
    assert "remains unresolved" in err and "verifier infrastructure error: OSError" in err
    assert _registry(workspace).list()[0].status == "open"


def test_failing_regression_test_keeps_the_issue_open(agent, workspace, capsys, monkeypatch):
    import tools.run_tests as run_tests_mod

    _reg, issue = _splitter_issue(workspace)
    monkeypatch.setattr(
        run_tests_mod,
        "RunTestsTool",
        FakeRunner(
            result={"exit_code": 1, "passed": 0, "failed": 1, "errors": 0, "timed_out": False}
        ),
    )

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True

    err = capsys.readouterr().err
    assert "remains open" in err and "failed=1" in err
    assert _registry(workspace).list()[0].status == "open"
    assert "self_improvement_issue_resolved" not in agent.log.kinds()


def test_passing_regression_test_verifies_then_resolves(agent, workspace, capsys, monkeypatch):
    import tools.run_tests as run_tests_mod

    _reg, issue = _splitter_issue(workspace)
    monkeypatch.setattr(
        run_tests_mod,
        "RunTestsTool",
        FakeRunner(
            result={"exit_code": 0, "passed": 3, "failed": 0, "errors": 0, "timed_out": False}
        ),
    )

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True

    err = capsys.readouterr().err
    assert f"issue resolved: {issue.fingerprint}" in err
    assert "3 passed" in err
    assert _registry(workspace).list()[0].status == "resolved"
    kinds = agent.log.kinds()
    assert kinds.index("self_improvement_issue_verified") < kinds.index(
        "self_improvement_issue_resolved"
    ), "verification is recorded before resolution"


def test_registry_refusing_the_transition_is_reported(agent, workspace, capsys, monkeypatch):
    import tools.run_tests as run_tests_mod
    from core.self_improvement_issues import SelfImprovementIssueRegistry

    _reg, issue = _splitter_issue(workspace)
    monkeypatch.setattr(
        run_tests_mod,
        "RunTestsTool",
        FakeRunner(
            result={"exit_code": 0, "passed": 1, "failed": 0, "errors": 0, "timed_out": False}
        ),
    )
    monkeypatch.setattr(SelfImprovementIssueRegistry, "transition", lambda *a, **k: None)

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True
    assert "lifecycle mismatch" in capsys.readouterr().err


def test_verification_accepted_but_resolution_refused_is_reported(
    agent, workspace, capsys, monkeypatch
):
    """The registry may accept `verified` and then refuse `resolved`.

    That leaves the issue *not* resolved, and the operator has to be told —
    silently printing "resolved" here would mark a live issue as closed.
    """
    import tools.run_tests as run_tests_mod
    from core.self_improvement_issues import SelfImprovementIssueRegistry

    _reg, issue = _splitter_issue(workspace)
    monkeypatch.setattr(
        run_tests_mod,
        "RunTestsTool",
        FakeRunner(
            result={"exit_code": 0, "passed": 1, "failed": 0, "errors": 0, "timed_out": False}
        ),
    )
    calls: list[str] = []

    def _transition(self, *, status, **kwargs):
        calls.append(status)
        return SimpleNamespace(status=status) if status == "verified" else None

    monkeypatch.setattr(SelfImprovementIssueRegistry, "transition", _transition)

    assert _handle_self_issue_verify(issue.fingerprint, agent, workspace) is True

    err = capsys.readouterr().err
    assert "remains unresolved" in err
    assert "refused resolution" in err
    assert calls == ["verified", "resolved"]
    assert "self_improvement_issue_verified" in agent.log.kinds()
    assert "self_improvement_issue_resolved" not in agent.log.kinds()


# ── remaining best-effort branches ───────────────────────────────────────────

def test_producer_ledger_records_an_approved_producer_item(agent, workspace, monkeypatch):
    import core.subagent_registry as reg_mod
    from core.self_build_producer import PRODUCER_ORIGIN

    calls: list[tuple] = []
    fake_registry = SimpleNamespace(
        apply_lane_outcome=lambda item_id, outcome: calls.append((item_id, outcome))
    )
    monkeypatch.setattr(reg_mod.SubagentRegistry, "load", staticmethod(lambda ws: fake_registry))

    item = add_item(
        agent, workspace, operation="self_apply_lane.run", payload={"origin": PRODUCER_ORIGIN}
    )
    assert _handle_approval_decision(item.id, agent, workspace, decision="approve") is True

    assert calls == [(item.id, "approved")]


def test_issue_registry_failure_does_not_break_the_advice(agent, workspace, capsys, monkeypatch):
    """Lifecycle storage is advisory input; losing it must not lose the advice."""
    import core.self_build_memory as sbm

    def _boom(agent_, ws):
        raise RuntimeError("registry unreadable")

    monkeypatch.setattr(sbm, "sync_self_improvement_issue_registry", _boom)
    monkeypatch.setattr(
        sbm, "recent_unresolved_self_improvement_failures", lambda agent_, ws: ("a failure",)
    )

    assert _handle_best_next_action("", agent, workspace) is True
    assert "best_next_action" in agent.log.kinds()
    assert capsys.readouterr().err.strip()
