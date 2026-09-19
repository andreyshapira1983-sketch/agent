"""`:self-task-propose` / `:self-task-build` — Stage A and Stage B of coding tasks.

Stage A turns a real `# TODO` into one coding task plus a **failing acceptance
test**, and drops a single approval item. Stage B implements it, only for an
already-approved id. Neither writes code without a human in between.

The property worth the most here is the anti-cheating guarantee of Stage A: the
command prints the **full proposed acceptance test** before the human approves
it. If that print quietly stops, the operator approves a yardstick they never
read — and the tests below fail loudly if it does.

Also pinned: both structured logs carry exactly five outcome keys and never the
generated test or implementation body; both stages journal every attempt; and
both refuse malformed input before the producer/builder runs.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cli.commands_self_task as mod
from cli.commands_self_task import _handle_self_task_build, _handle_self_task_propose

LOG_KEYS = {"status", "target_path", "approval_id", "checked_gates", "veto_reasons"}


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
    return SimpleNamespace(
        log=FakeLog(),
        model_router=SimpleNamespace(for_role=lambda role: f"llm:{role}"),
    )


class FakeInbox:
    def __init__(self, items=()):
        self._items = list(items)

    def list(self):
        return list(self._items)


def item(item_id="ain-1", *, test_path="tests/test_new.py", payload=None):
    return SimpleNamespace(id=item_id, payload={"test_path": test_path, **(payload or {})})


@pytest.fixture
def env(monkeypatch):
    """Neutralise the runtime; expose what was captured."""
    captured: dict = {"calls": [], "episodes": [], "inbox": FakeInbox()}

    monkeypatch.setattr(mod, "_budget_ledger_snapshot", lambda agent: {"windows": []})
    monkeypatch.setattr(
        mod, "BudgetKillSwitch", lambda path: SimpleNamespace(status=lambda snap: "inactive")
    )
    monkeypatch.setattr(mod, "default_path", lambda ws: ws / "kill.json")
    monkeypatch.setattr(mod, "SafeVCS", lambda workspace: "VCS")
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: captured["inbox"])
    monkeypatch.setattr(
        mod,
        "record_self_build_episode",
        lambda agent, *, kind, result: captured["episodes"].append({"kind": kind, "result": result}),
    )
    monkeypatch.setattr(mod, "decode_frozen_test", lambda payload: payload.get("frozen", ""))
    return captured


def use_producer(monkeypatch, env, result: dict, *, name="produce_coding_task"):
    def _run(**kwargs):
        env["calls"].append(kwargs)
        return SimpleNamespace(to_dict=lambda: result)

    monkeypatch.setattr(mod, name, _run)


# ── :self-task-propose — arguments and wiring ────────────────────────────────

@pytest.mark.parametrize("rest", ["core/foo.py", "--force", "fix the TODO in loop.py"])
def test_propose_takes_no_arguments(rest, agent, tmp_path, capsys, monkeypatch, env):
    monkeypatch.setattr(
        mod, "produce_coding_task", lambda **k: pytest.fail("the producer must not run")
    )

    assert _handle_self_task_propose(rest, agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "Usage: :self-task-propose" in err
    assert "no arguments" in err
    assert agent.log.events == []
    assert env["episodes"] == []


def test_propose_wires_the_gates_into_the_producer(agent, tmp_path, monkeypatch, env):
    use_producer(monkeypatch, env, {"status": "no_task", "reason": "no TODO found"})

    assert _handle_self_task_propose("", agent, tmp_path) is True

    call = env["calls"][0]
    assert call["kill_switch"] == "inactive", "the kill-switch state reaches the producer"
    assert call["budget_snapshot"] == {"windows": []}
    assert call["llm"] == "llm:synthesizer"
    assert call["vcs"] == "VCS"
    assert call["workspace"] == tmp_path


# ── the anti-cheating guarantee ──────────────────────────────────────────────

def test_the_proposed_acceptance_test_is_shown_in_full(agent, tmp_path, capsys, monkeypatch, env):
    """The human must be able to read the yardstick before approving it."""
    frozen = (
        "def test_parses_empty_input():\n"
        "    assert parse('') == []\n"
        "    # example PII kept verbatim: alice@example.com\n"
    )
    env["inbox"] = FakeInbox([item("ain-7", payload={"frozen": frozen})])
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: env["inbox"])
    use_producer(
        monkeypatch,
        env,
        {
            "status": "proposed",
            "reason": "one TODO turned into a task",
            "target_path": "core/parser.py",
            "approval_id": "ain-7",
            "next_human_action": ":approval-approve ain-7",
        },
    )

    assert _handle_self_task_propose("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "--- proposed acceptance test (READ before approving) ---" in err
    assert "# tests/test_new.py" in err
    assert "def test_parses_empty_input():" in err
    assert "alice@example.com" in err, "the frozen copy is shown verbatim, not redacted"
    assert "--- end of test ---" in err


def test_no_approval_id_means_no_test_block(agent, tmp_path, capsys, monkeypatch, env):
    use_producer(monkeypatch, env, {"status": "no_task", "reason": "nothing to do"})

    assert _handle_self_task_propose("", agent, tmp_path) is True
    assert "proposed acceptance test" not in capsys.readouterr().err


def test_an_approval_id_with_no_matching_item_prints_no_block(
    agent, tmp_path, capsys, monkeypatch, env
):
    env["inbox"] = FakeInbox([item("ain-other", payload={"frozen": "def test_x(): pass"})])
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: env["inbox"])
    use_producer(monkeypatch, env, {"status": "proposed", "approval_id": "ain-missing"})

    assert _handle_self_task_propose("", agent, tmp_path) is True
    assert "proposed acceptance test" not in capsys.readouterr().err


def test_a_blank_frozen_test_prints_no_block(agent, tmp_path, capsys, monkeypatch, env):
    env["inbox"] = FakeInbox([item("ain-8", payload={"frozen": "   \n  "})])
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: env["inbox"])
    use_producer(monkeypatch, env, {"status": "proposed", "approval_id": "ain-8"})

    assert _handle_self_task_propose("", agent, tmp_path) is True
    assert "proposed acceptance test" not in capsys.readouterr().err


# ── propose: reporting and logging ───────────────────────────────────────────

def test_propose_reports_every_optional_line_when_present(
    agent, tmp_path, capsys, monkeypatch, env
):
    use_producer(
        monkeypatch,
        env,
        {
            "status": "vetoed",
            "reason": "critic refused the test",
            "target_path": "core/parser.py",
            "approval_id": "ain-9",
            "veto_reasons": ["test asserts nothing"],
            "roles": [
                {"role": "producer", "decision": "draft"},
                {"role": "critic", "decision": "veto"},
            ],
            "next_human_action": "nothing to approve",
        },
    )
    env["inbox"] = FakeInbox([])
    monkeypatch.setattr(mod, "_approval_inbox_for", lambda agent, ws: env["inbox"])

    assert _handle_self_task_propose("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "impl: core/parser.py" in err
    assert "approval_id: ain-9" in err
    assert "veto_reasons: ['test asserts nothing']" in err
    assert "roles: producer:draft, critic:veto" in err
    assert "next: nothing to approve" in err


def test_propose_omits_optional_lines_when_absent(agent, tmp_path, capsys, monkeypatch, env):
    use_producer(monkeypatch, env, {"status": "no_task", "reason": "no TODO found"})

    assert _handle_self_task_propose("", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "status: no_task" in err
    for absent in ("impl:", "approval_id:", "veto_reasons:", "roles:"):
        assert absent not in err


def test_propose_log_is_five_keys_and_carries_no_test_body(agent, tmp_path, monkeypatch, env):
    use_producer(
        monkeypatch,
        env,
        {
            "status": "proposed",
            "target_path": "core/parser.py",
            "approval_id": "ain-1",
            "checked_gates": ["allowlist"],
            "veto_reasons": [],
            "frozen_test": "def test_secret_yardstick(): assert False",
            "reason": "ok",
        },
    )

    assert _handle_self_task_propose("", agent, tmp_path) is True

    logged = agent.log.payload("self_task_produce")
    assert set(logged) == LOG_KEYS
    assert "test_secret_yardstick" not in json.dumps(logged)


def test_propose_journals_every_attempt(agent, tmp_path, monkeypatch, env):
    result = {"status": "no_task", "reason": "no TODO found"}
    use_producer(monkeypatch, env, result)

    assert _handle_self_task_propose("", agent, tmp_path) is True
    assert env["episodes"] == [{"kind": "self-task-produce", "result": result}]


# ── :self-task-build ─────────────────────────────────────────────────────────

@pytest.mark.parametrize("rest", ["", "   ", "ain-1 ain-2", "ain-1 --force"])
def test_build_requires_exactly_one_id(rest, agent, tmp_path, capsys, monkeypatch, env):
    monkeypatch.setattr(
        mod, "build_coding_task", lambda **k: pytest.fail("the builder must not run")
    )

    assert _handle_self_task_build(rest, agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "Usage: :self-task-build <approval_id>" in err
    assert "APPROVED coding task" in err
    assert agent.log.events == []
    assert env["episodes"] == []


def test_build_wires_the_id_and_the_gates(agent, tmp_path, monkeypatch, env):
    use_producer(monkeypatch, env, {"status": "built", "reason": "ok"}, name="build_coding_task")

    assert _handle_self_task_build("ain-5", agent, tmp_path) is True

    call = env["calls"][0]
    assert call["approval_id"] == "ain-5"
    assert call["kill_switch"] == "inactive"
    assert call["budget_snapshot"] == {"windows": []}
    assert call["llm"] == "llm:synthesizer"


def test_build_reports_and_logs_its_outcome(agent, tmp_path, capsys, monkeypatch, env):
    use_producer(
        monkeypatch,
        env,
        {
            "status": "built",
            "reason": "implementation passes the frozen test",
            "target_path": "core/parser.py",
            "approval_id": "ain-5",
            "checked_gates": ["frozen_test_unchanged"],
            "veto_reasons": [],
            "roles": [{"role": "builder", "decision": "done"}],
            "implementation": "SECRET-IMPL-BODY",
            "next_human_action": ":self-apply-run ain-5",
        },
        name="build_coding_task",
    )

    assert _handle_self_task_build("ain-5", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "=== self-task build ===" in err
    assert "impl: core/parser.py" in err
    assert "roles: builder:done" in err
    assert "next: :self-apply-run ain-5" in err
    assert "SECRET-IMPL-BODY" not in err, "the implementation body is not printed"

    logged = agent.log.payload("self_task_build")
    assert set(logged) == LOG_KEYS
    assert "SECRET-IMPL-BODY" not in json.dumps(logged)


def test_build_shows_why_it_was_vetoed(agent, tmp_path, capsys, monkeypatch, env):
    use_producer(
        monkeypatch,
        env,
        {
            "status": "vetoed",
            "reason": "the builder touched the frozen test",
            "target_path": "core/parser.py",
            "veto_reasons": ["frozen acceptance test was modified"],
            "next_human_action": "nothing was applied",
        },
        name="build_coding_task",
    )

    assert _handle_self_task_build("ain-5", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "veto_reasons: ['frozen acceptance test was modified']" in err
    assert agent.log.payload("self_task_build")["veto_reasons"] == [
        "frozen acceptance test was modified"
    ]


def test_build_journals_every_attempt(agent, tmp_path, monkeypatch, env):
    result = {"status": "refused", "reason": "approval not found"}
    use_producer(monkeypatch, env, result, name="build_coding_task")

    assert _handle_self_task_build("ain-nope", agent, tmp_path) is True
    assert env["episodes"] == [{"kind": "self-task-build", "result": result}]


def test_build_omits_optional_lines_when_absent(agent, tmp_path, capsys, monkeypatch, env):
    use_producer(
        monkeypatch, env, {"status": "refused", "reason": "not approved"}, name="build_coding_task"
    )

    assert _handle_self_task_build("ain-5", agent, tmp_path) is True

    err = capsys.readouterr().err
    for absent in ("impl:", "approval_id:", "veto_reasons:", "roles:"):
        assert absent not in err
