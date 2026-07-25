"""`:self-build-produce` and `:self-build-supervisor` — the operator's view of self-building.

Neither command may change the project: produce creates at most one approval
item, the supervisor is read-only. What was untested at 62% is what the operator
actually *sees* — and seeing the wrong thing here is a safety problem of its own:

* a run that found no grounded backlog candidate must say so, with the precise
  reason, instead of looking like a run that simply produced nothing;
* the structured log must carry the outcome without ever dumping generated file
  content or a candidate diff;
* the evidence collectors (`TECH_DEBT.md` digest, recent error scan) are
  best-effort by contract: a missing or corrupt file yields an empty result, not
  an exception that takes the supervisor down with it.

The producer and the supervisor evaluator themselves are faked — running them
for real would call a model and write to the inbox, which these commands are
supposed to gate, not perform.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.commands_self_build as mod
from cli.commands_self_build import (
    _handle_self_build_produce,
    _handle_self_build_supervisor,
    _recent_error_lines,
    _tech_debt_summary,
)


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def payload(self, event: str) -> dict:
        for name, data in self.events:
            if name == event:
                return data or {}
        raise AssertionError(f"event {event!r} was never logged: {[e for e, _ in self.events]}")


@pytest.fixture
def agent():
    return SimpleNamespace(
        log=FakeLog(),
        model_router=SimpleNamespace(for_role=lambda role: f"llm:{role}"),
    )


@pytest.fixture
def produce_env(monkeypatch):
    """Neutralise everything the produce command touches except the report."""
    monkeypatch.setattr(mod, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        mod, "BudgetKillSwitch", lambda path: SimpleNamespace(status=lambda snap: "inactive")
    )
    monkeypatch.setattr(mod, "default_path", lambda ws: ws / "kill")
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: "INBOX")
    monkeypatch.setattr(mod, "SafeVCS", lambda workspace: "VCS")
    monkeypatch.setattr(mod, "recent_self_build_lessons", lambda agent, target: ())
    monkeypatch.setattr(mod, "recently_vetoed_self_build_targets", lambda agent: ())
    episodes: list[dict] = []
    monkeypatch.setattr(
        mod,
        "record_self_build_episode",
        lambda agent, *, kind, result: episodes.append({"kind": kind, "result": result}),
    )
    return episodes


def _report(**payload):
    return SimpleNamespace(to_dict=lambda: payload)


def _use_report(monkeypatch, payload: dict, captured: dict | None = None):
    def _produce(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return _report(**payload)

    monkeypatch.setattr(mod, "produce_self_apply_proposal", _produce)


# ── :self-build-produce ──────────────────────────────────────────────────────

@pytest.mark.parametrize("rest", ["core/loop.py", "--force", "please fix the parser"])
def test_produce_takes_no_arguments(rest, agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        mod, "produce_self_apply_proposal", lambda **k: pytest.fail("producer must not run")
    )
    assert _handle_self_build_produce(rest, agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "Usage: :self-build-produce" in err
    assert "no arguments" in err
    assert agent.log.events == []


def test_produce_passes_the_gate_state_into_the_producer(agent, tmp_path, produce_env, monkeypatch):
    captured: dict = {}
    _use_report(monkeypatch, {"status": "no_patch", "reason": "nothing to do"}, captured)

    assert _handle_self_build_produce("", agent, tmp_path) is True

    assert captured["budget_snapshot"] == {"windows": []}
    assert captured["kill_switch"] == "inactive", "the kill-switch state reaches the producer"
    assert captured["inbox"] == "INBOX"
    assert captured["llm"] == "llm:synthesizer"
    assert captured["max_builder_attempts"] == 2


def test_grounded_run_names_its_evidence(agent, tmp_path, capsys, produce_env, monkeypatch):
    _use_report(
        monkeypatch,
        {
            "status": "proposed",
            "reason": "one candidate",
            "target_path": "core/foo.py",
            "approval_id": "ap-1",
            "attempts": 1,
            "checked_gates": ["allowlist"],
            "roles": [
                {
                    "role": "manager",
                    "decision": "target",
                    "data": {"grounded": True, "evidence_ref": "TECH_DEBT.md#TD-042"},
                },
                {"role": "critic", "decision": "accept"},
            ],
            "next_human_action": ":approval-list",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "manager: grounded backlog candidate" in err
    assert "evidence_ref: TECH_DEBT.md#TD-042" in err
    assert "target: core/foo.py" in err
    assert "approval_id: ap-1" in err
    assert "roles: manager:target, critic:accept" in err
    assert "next: :approval-list" in err

    logged = agent.log.payload("self_build_produce")
    assert logged["grounded"] is True
    assert logged["no_grounded_target"] is False
    assert logged["approval_id"] == "ap-1"


def test_grounded_run_without_an_evidence_ref_omits_the_line(
    agent, tmp_path, capsys, produce_env, monkeypatch
):
    _use_report(
        monkeypatch,
        {
            "status": "proposed",
            "reason": "ok",
            "roles": [{"role": "manager", "decision": "target", "data": {"grounded": True}}],
            "next_human_action": "review",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "manager: grounded backlog candidate" in err
    assert "evidence_ref:" not in err, "an empty reference is omitted, not printed blank"
    assert agent.log.payload("self_build_produce")["evidence_ref"] == ""


def test_no_grounded_target_reports_the_precise_reason(
    agent, tmp_path, capsys, produce_env, monkeypatch
):
    """"No patch" and "no verifiable candidate" are different outcomes."""
    _use_report(
        monkeypatch,
        {
            "status": "no_patch",
            "reason": "manager found nothing publishable",
            "roles": [
                {
                    "role": "manager",
                    "decision": "no_target",
                    "detail": "candidate core/loop.py is off-allowlist",
                    "data": {"grounded": False},
                }
            ],
            "next_human_action": "extend the allowlist or pick another target",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "manager: no grounded target (candidate core/loop.py is off-allowlist)" in err
    assert "target:" not in err and "approval_id:" not in err

    logged = agent.log.payload("self_build_produce")
    assert logged["no_grounded_target"] is True
    assert logged["grounded"] is False


def test_no_grounded_target_without_a_detail_falls_back_to_a_generic_reason(
    agent, tmp_path, capsys, produce_env, monkeypatch
):
    _use_report(
        monkeypatch,
        {
            "status": "no_grounded_target",
            "reason": "empty backlog",
            "roles": [{"role": "manager", "decision": "no_target", "data": {"grounded": False}}],
            "next_human_action": "add a TD entry",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True
    assert "manager: no grounded target (no verifiable grounded candidate)" in (
        capsys.readouterr().err
    )


def test_veto_reasons_are_shown_and_logged(agent, tmp_path, capsys, produce_env, monkeypatch):
    _use_report(
        monkeypatch,
        {
            "status": "vetoed",
            "reason": "critic refused",
            "target_path": "core/foo.py",
            "veto_reasons": ["touches a critical path"],
            "roles": [{"role": "critic", "decision": "veto"}],
            "next_human_action": "nothing",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "veto_reasons: ['touches a critical path']" in err
    assert agent.log.payload("self_build_produce")["veto_reasons"] == ["touches a critical path"]


def test_a_report_with_no_roles_still_prints_and_journals(
    agent, tmp_path, capsys, produce_env, monkeypatch
):
    _use_report(monkeypatch, {"status": "error", "reason": "producer crashed", "roles": []})

    assert _handle_self_build_produce("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "status: error" in err
    assert "roles:" not in err
    assert "manager:" not in err
    assert produce_env == [
        {"kind": "self-build-produce", "result": {"status": "error", "reason": "producer crashed", "roles": []}}
    ], "every attempt is journalled, including the failures"


def test_the_log_never_carries_generated_content(agent, tmp_path, produce_env, monkeypatch):
    """The structured log is an outcome record, not a copy of the patch."""
    _use_report(
        monkeypatch,
        {
            "status": "proposed",
            "reason": "ok",
            "target_path": "core/foo.py",
            "proposed_content": "SECRET-PATCH-BODY",
            "diff": "--- a\n+++ b\nSECRET-PATCH-BODY",
            "roles": [],
            "next_human_action": "review",
        },
    )

    assert _handle_self_build_produce("", agent, tmp_path) is True

    logged = agent.log.payload("self_build_produce")
    assert "SECRET-PATCH-BODY" not in json.dumps(logged)
    assert set(logged) == {
        "status", "target_path", "approval_id", "checked_gates", "veto_reasons",
        "grounded", "evidence_ref", "no_grounded_target", "attempts",
    }


# ── _tech_debt_summary ───────────────────────────────────────────────────────

def test_tech_debt_summary_when_the_file_is_missing(tmp_path):
    assert _tech_debt_summary(tmp_path) == {"present": False}


def test_tech_debt_summary_counts_open_and_done(tmp_path):
    (tmp_path / "TECH_DEBT.md").write_text(
        "\n".join(
            [
                "# Tech debt",
                "TD-001 first item",
                "Статус: done (merged)",
                "TD-002 second item",
                "Статус: in progress",
                "TD-003 third item",
                "  Статус : DONE",
                "TD-004 no status line at all",
                "some prose that is not a status",
            ]
        ),
        encoding="utf-8",
    )

    assert _tech_debt_summary(tmp_path) == {
        "present": True,
        "total": 4,
        "done": 2,
        "open": 1,
    }


# ── _recent_error_lines ──────────────────────────────────────────────────────

def test_recent_errors_without_a_log_directory(tmp_path):
    assert _recent_error_lines(tmp_path) == []


def test_recent_errors_with_an_empty_log_directory(tmp_path):
    (tmp_path / "logs").mkdir()
    assert _recent_error_lines(tmp_path) == []


def test_recent_errors_picks_error_shaped_events_newest_first(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "trace.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"event": "observe"}),
                "   ",
                "not json at all",
                json.dumps({"event": "tool_failed"}),
                json.dumps({"no_name": 1}),
                json.dumps({"type": "policy_blocked"}),
                json.dumps({"event": "verifier_error"}),
            ]
        ),
        encoding="utf-8",
    )

    assert _recent_error_lines(tmp_path) == ["verifier_error", "policy_blocked", "tool_failed"]


def test_recent_errors_stop_at_the_cap(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "trace.jsonl").write_text(
        "\n".join(json.dumps({"event": f"error_{i}"}) for i in range(10)), encoding="utf-8"
    )

    assert _recent_error_lines(tmp_path, max_errors=3) == ["error_9", "error_8", "error_7"]


def test_recent_errors_reads_the_newest_log_file(tmp_path):
    logs = tmp_path / "logs"
    logs.mkdir()
    old = logs / "old.jsonl"
    new = logs / "new.jsonl"
    old.write_text(json.dumps({"event": "old_error"}), encoding="utf-8")
    new.write_text(json.dumps({"event": "new_error"}), encoding="utf-8")
    import os

    os.utime(old, (1_600_000_000, 1_600_000_000))
    os.utime(new, (1_700_000_000, 1_700_000_000))

    assert _recent_error_lines(tmp_path) == ["new_error"]


def test_recent_errors_survive_an_unlistable_log_directory(tmp_path, monkeypatch):
    """Even listing the directory may fail — the supervisor must still run."""
    (tmp_path / "logs").mkdir()

    def _unlistable(self, pattern):
        raise OSError("permission denied")

    monkeypatch.setattr(Path, "glob", _unlistable)
    assert _recent_error_lines(tmp_path) == []


def test_recent_errors_survive_an_unreadable_log(tmp_path, monkeypatch):
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "trace.jsonl").write_text(json.dumps({"event": "some_error"}), encoding="utf-8")

    def _unreadable(self, *a, **k):
        raise OSError("locked")

    monkeypatch.setattr(Path, "read_text", _unreadable)
    assert _recent_error_lines(tmp_path) == []


# ── :self-build-supervisor ───────────────────────────────────────────────────

@pytest.fixture
def supervisor_env(monkeypatch):
    monkeypatch.setattr(mod, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        mod,
        "_approval_inbox_for",
        lambda agent, ws: SimpleNamespace(snapshot=lambda: {"pending": 2}),
    )
    monkeypatch.setattr(
        mod, "_task_queue_for", lambda agent, ws: SimpleNamespace(summary=lambda: {"pending": 0})
    )
    monkeypatch.setattr(
        mod, "_scheduler_for", lambda agent, ws: SimpleNamespace(summary=lambda: {"jobs": 0})
    )
    monkeypatch.setattr(mod, "_self_build_propose_payload", lambda ws: {"file": "core/foo.py"})


def _use_supervisor(monkeypatch, report: dict, captured: dict | None = None):
    def _evaluate(**kwargs):
        if captured is not None:
            captured.update(kwargs)
        return report

    monkeypatch.setattr(mod, "evaluate_self_build_supervisor", _evaluate)


def test_supervisor_collects_read_only_evidence(agent, tmp_path, supervisor_env, monkeypatch, capsys):
    captured: dict = {}
    _use_supervisor(
        monkeypatch,
        {"status": "wait", "reason": "inbox busy", "checked_sections": ["budget", "inbox"],
         "candidate": None, "recommended_next_action": "drain the inbox"},
        captured,
    )

    assert _handle_self_build_supervisor("", agent, tmp_path) is True

    assert captured["approvals_pending"] == 2
    assert captured["budget_windows"] == {"windows": []}
    assert captured["task_queue"] == {"pending": 0}
    assert callable(captured["candidate_provider"]), "the candidate is fetched lazily"

    err = capsys.readouterr().err
    assert "status: wait" in err
    assert "checked: budget, inbox" in err
    assert "candidate: None" in err
    assert "next: drain the inbox" in err


def test_supervisor_prints_a_candidate_without_its_diff(
    agent, tmp_path, supervisor_env, monkeypatch, capsys
):
    _use_supervisor(
        monkeypatch,
        {
            "status": "propose",
            "reason": "one grounded candidate",
            "checked_sections": ["backlog"],
            "candidate": {
                "file": "core/foo.py",
                "diagnosis": "duplicated branch",
                "diff": "--- a\n+++ b\nSECRET-DIFF-BODY",
            },
            "recommended_next_action": ":self-build-produce",
        },
    )

    assert _handle_self_build_supervisor("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "candidate file: core/foo.py" in err
    assert "candidate diagnosis: duplicated branch" in err
    assert "SECRET-DIFF-BODY" not in err, "the human summary never prints the diff"

    logged = agent.log.payload("self_build_supervisor")
    assert logged["candidate"] == {"file": "core/foo.py", "has_diff": True}
    assert "SECRET-DIFF-BODY" not in json.dumps(logged)


def test_supervisor_marks_a_no_patch_candidate_as_diffless(
    agent, tmp_path, supervisor_env, monkeypatch, capsys
):
    _use_supervisor(
        monkeypatch,
        {
            "status": "wait",
            "reason": "candidate has no patch",
            "checked_sections": [],
            "candidate": {"file": "core/foo.py", "diagnosis": "unclear", "diff": "NO_PATCH"},
            "recommended_next_action": "wait",
        },
    )

    assert _handle_self_build_supervisor("", agent, tmp_path) is True
    assert agent.log.payload("self_build_supervisor")["candidate"]["has_diff"] is False


def test_supervisor_json_mode_is_parsable(agent, tmp_path, supervisor_env, monkeypatch, capsys):
    report = {
        "status": "stop",
        "reason": "kill switch",
        "checked_sections": ["budget"],
        "candidate": None,
        "recommended_next_action": "clear the kill switch",
    }
    _use_supervisor(monkeypatch, report)

    assert _handle_self_build_supervisor("--json", agent, tmp_path) is True

    assert json.loads(capsys.readouterr().err) == report
    assert agent.log.payload("self_build_supervisor")["status"] == "stop"
