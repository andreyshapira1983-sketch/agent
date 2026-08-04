"""The eleven miscellaneous operator commands — flags, refusals, and output modes.

`cli/commands_misc.py` разошёлся на пять модулей по темам (команда агентов,
коннекторы, аудиты, обучение, разбор знаний) — этот файл проверяет их все,
поэтому назван по предмету, а не по бывшему файлу.

Каждый обработчик — почти целиком разбор аргументов перед вызовом `core`
report: `:connectors`, `:connector-plan`, `:architecture-audit`, `:learn`,
`:conflicts`, `:state-store-drill`, `:release-audit`, `:supply-chain-audit`,
`:team-plan`, `:team-run`, `:assumptions`. At 63% the untested part was exactly
the parsing — every usage error, every `--json` variant, and the two places
where a wrong flag would otherwise reach a real audit or a real subagent run.

Two things these tests hold onto:

* a malformed flag stops the command **before** the expensive call — the fakes
  fail the test if they are invoked;
* `--json` prints parsable JSON, and the human mode prints the summary — a
  command that silently prints nothing is a bug the old coverage could not see.

The heavy `core` collaborators are faked at the module boundary; the pure ones
(the connector registry and planner) run for real.
"""
from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

# Цели подмены — модули, в чьих globals имя РАЗРЕШАЕТСЯ. `commands_misc`
# разошёлся по темам, и один псевдоним `mod` стал тремя.
import cli.commands_audit as audit_mod
import cli.commands_knowledge_review as review_mod
import cli.commands_learn as learn_mod
import cli.commands_team as team_mod
from cli.commands_audit import (
    _handle_architecture_audit,
    _handle_release_audit,
)
from cli.commands_connectors import _handle_connector_plan, _handle_connectors
from cli.commands_knowledge_review import _handle_assumptions, _handle_conflicts
from cli.commands_learn import _handle_learn
from cli.commands_team import _handle_team_plan, _handle_team_run


class FakeLog:
    def __init__(self, log_dir: Path | None = None, trace_id: str = "run_current") -> None:
        self.events: list[tuple[str, object]] = []
        self.log_dir = log_dir
        self.trace_id = trace_id

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self):
        return [e for e, _ in self.events]


@pytest.fixture
def agent(tmp_path):
    return SimpleNamespace(log=FakeLog(log_dir=tmp_path / "logs"))


class FakeReport:
    """A `core` report: dict payload, human summary."""

    def __init__(self, payload: dict | None = None, summary: str = "SUMMARY"):
        self._payload = payload or {"ok": True}
        self._summary = summary

    def to_dict(self):
        return self._payload

    def user_summary(self, **kwargs):
        return self._summary


def never(*a, **k):
    raise AssertionError("this collaborator must not be reached on a usage error")


# ── :connectors ──────────────────────────────────────────────────────────────

def test_connectors_lists_every_connector(capsys):
    assert _handle_connectors("") is True
    err = capsys.readouterr().err
    assert "=== source connectors ===" in err
    assert "commands=" in err and "use:" in err


@pytest.mark.parametrize("rest", ["nonsense", "wired planned"])
def test_connectors_refuses_a_bad_status(rest, capsys):
    assert _handle_connectors(rest) is True
    assert "Usage: :connectors [all|wired|partial|planned]" in capsys.readouterr().err


def test_connectors_json_is_parsable_and_filtered(capsys):
    assert _handle_connectors("--json") is True
    everything = json.loads(capsys.readouterr().err)

    assert _handle_connectors("wired --json") is True
    wired = json.loads(capsys.readouterr().err)

    assert {c["status"] for c in wired["connectors"]} <= {"wired"}
    assert len(wired["connectors"]) <= len(everything["connectors"])


# ── :connector-plan ──────────────────────────────────────────────────────────

def test_connector_plan_without_a_goal_prints_usage(capsys):
    assert _handle_connector_plan("--json") is True
    assert "Usage: :connector-plan <goal>" in capsys.readouterr().err


@pytest.mark.parametrize("rest", ["watch releases --limit", "watch releases --limit many"])
def test_connector_plan_refuses_a_bad_limit(rest, capsys):
    assert _handle_connector_plan(rest) is True
    assert "Usage: --limit requires a number" in capsys.readouterr().err


def test_connector_plan_prints_a_summary_and_json(capsys):
    assert _handle_connector_plan("monitor python releases --limit 2") is True
    assert capsys.readouterr().err.strip()

    assert _handle_connector_plan("monitor python releases --json") is True
    assert isinstance(json.loads(capsys.readouterr().err), dict)


# ── :architecture-audit ──────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--limit", "Usage: --limit requires a number"),
        ("--limit two", "Usage: --limit requires a number"),
        ("--wat", "unknown :architecture-audit option --wat"),
        ("--limit 0", "Usage: --limit must be >= 1"),
    ],
)
def test_architecture_audit_usage_errors_never_run_the_audit(
    rest, expected, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(audit_mod, "audit_architecture", never)
    assert _handle_architecture_audit(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err
    assert agent.log.kinds() == []


def test_architecture_audit_logs_and_prints_both_modes(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        audit_mod, "audit_architecture", lambda ws: FakeReport({"findings": []}, "AUDIT-TEXT")
    )

    assert _handle_architecture_audit("--limit 3", agent, tmp_path) is True
    assert "AUDIT-TEXT" in capsys.readouterr().err

    assert _handle_architecture_audit("--json", agent, tmp_path) is True
    assert json.loads(capsys.readouterr().err) == {"findings": []}
    assert agent.log.kinds() == ["architecture_audit", "architecture_audit"]


# ── :learn ───────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--limit", "Usage: --limit requires a number"),
        ("--limit lots", "Usage: --limit requires a number"),
        ("--root", "Usage: --root requires a path"),
    ],
)
def test_learn_usage_errors_never_plan_or_ingest(
    rest, expected, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(learn_mod, "LearningPlanner", never)
    monkeypatch.setattr(learn_mod, "ingest_files", never)
    assert _handle_learn(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def _planner_returning(paths):
    plan = SimpleNamespace(
        source_paths=paths,
        to_log_payload=lambda: {"paths": list(paths)},
        user_summary=lambda: "PLAN-TEXT",
    )
    return lambda: SimpleNamespace(plan=lambda **kwargs: plan)


def test_learn_stops_when_the_plan_has_no_sources(agent, tmp_path, capsys, monkeypatch):
    """No sources means no ingestion pass at all.

    The fake *records* instead of raising: a raising fake would be swallowed by
    the handler's own `except Exception` and the test would pass while ingestion
    actually ran.
    """
    calls: list[dict] = []
    monkeypatch.setattr(learn_mod, "LearningPlanner", _planner_returning([]))
    monkeypatch.setattr(learn_mod, "ingest_files", lambda **kw: calls.append(kw) or FakeReport())

    assert _handle_learn("study the repo", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "PLAN-TEXT" in err
    assert calls == [], "ingestion must not run for an empty plan"
    assert "SUMMARY" not in err, "no ingestion report may be printed"
    assert agent.log.kinds() == ["learning_plan"]


def test_learn_ingests_the_planned_sources(agent, tmp_path, capsys, monkeypatch):
    seen: dict = {}

    def _ingest(**kwargs):
        seen.update(kwargs)
        return FakeReport(summary="INGEST-TEXT")

    monkeypatch.setattr(learn_mod, "LearningPlanner", _planner_returning(["a.py"]))
    monkeypatch.setattr(learn_mod, "ingest_files", _ingest)

    assert _handle_learn("study --dry-run --no-memory --limit 3 --root src", agent, tmp_path) is True

    err = capsys.readouterr().err
    assert "PLAN-TEXT" in err and "INGEST-TEXT" in err
    assert seen["dry_run"] is True
    assert seen["auto_write_memory"] is False
    assert seen["paths"] == ["a.py"]


def test_learn_write_memory_flag_is_forwarded(agent, tmp_path, monkeypatch):
    seen: dict = {}
    monkeypatch.setattr(learn_mod, "LearningPlanner", _planner_returning(["a.py"]))
    monkeypatch.setattr(learn_mod, "ingest_files", lambda **kw: seen.update(kw) or FakeReport())

    assert _handle_learn("study --write-memory", agent, tmp_path) is True
    assert seen["auto_write_memory"] is True
    assert seen["dry_run"] is False


def test_learn_reports_a_planner_failure_instead_of_raising(agent, tmp_path, capsys, monkeypatch):
    def _boom():
        raise RuntimeError("planner unavailable")

    monkeypatch.setattr(learn_mod, "LearningPlanner", _boom)
    assert _handle_learn("study", agent, tmp_path) is True
    assert "learn failed: RuntimeError: planner unavailable" in capsys.readouterr().err


# ── :conflicts ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--limit", "Usage: --limit requires a number"),
        ("--limit x", "Usage: --limit requires a number"),
        ("--nope", "unknown :conflicts option --nope"),
        ("--limit 0", "Usage: --limit must be >= 1"),
    ],
)
def test_conflicts_usage_errors_never_review(rest, expected, agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(review_mod, "ConflictReview", never)
    assert _handle_conflicts(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def test_conflicts_without_any_registry_says_so(agent, tmp_path, capsys):
    assert _handle_conflicts("", agent, tmp_path) is True
    assert "(no source registry available)" in capsys.readouterr().err


def test_conflicts_falls_back_to_the_last_run_registry(agent, tmp_path, capsys, monkeypatch):
    """An empty stored registry must not hide the registry of the last run."""
    empty = SimpleNamespace(sources=[], claims=[])
    last = SimpleNamespace(sources=["s"], claims=["c"])
    agent.source_registry_store = SimpleNamespace(load_registry=lambda: empty)
    agent.last_source_registry = last

    reviewed: list = []
    monkeypatch.setattr(
        review_mod,
        "ConflictReview",
        lambda: SimpleNamespace(
            review=lambda registry: reviewed.append(registry) or FakeReport(summary="CONFLICTS")
        ),
    )

    assert _handle_conflicts("", agent, tmp_path) is True

    assert reviewed == [last]
    assert "CONFLICTS" in capsys.readouterr().err
    assert "conflict_review" in agent.log.kinds()


def test_conflicts_json_mode(agent, tmp_path, capsys, monkeypatch):
    agent.source_registry_store = SimpleNamespace(
        load_registry=lambda: SimpleNamespace(sources=["s"], claims=["c"])
    )
    monkeypatch.setattr(
        review_mod,
        "ConflictReview",
        lambda: SimpleNamespace(review=lambda registry: FakeReport({"conflicts": 0})),
    )

    assert _handle_conflicts("--json --limit 3", agent, tmp_path) is True
    assert json.loads(capsys.readouterr().err) == {"conflicts": 0}


# ── the three single-flag audits ─────────────────────────────────────────────

AUDITS = [
    ("_handle_state_store_drill", "run_state_store_drill", ":state-store-drill", "state_store_recovery_drill"),
    ("_handle_supply_chain_audit", "audit_supply_chain", ":supply-chain-audit", "supply_chain_audit"),
]


@pytest.mark.parametrize("handler_name,dep,usage,event", AUDITS)
def test_single_flag_audit_rejects_extra_arguments(
    handler_name, dep, usage, event, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(audit_mod, dep, never)
    handler = getattr(audit_mod, handler_name)
    assert handler("--verbose", agent, tmp_path) is True
    assert f"Usage: {usage} [--json]" in capsys.readouterr().err


@pytest.mark.parametrize("handler_name,dep,usage,event", AUDITS)
def test_single_flag_audit_prints_both_modes_and_logs(
    handler_name, dep, usage, event, agent, tmp_path, capsys, monkeypatch
):
    monkeypatch.setattr(audit_mod, dep, lambda ws: FakeReport({"status": "ok"}, "AUDIT"))
    handler = getattr(audit_mod, handler_name)

    assert handler("", agent, tmp_path) is True
    assert "AUDIT" in capsys.readouterr().err

    assert handler("--json", agent, tmp_path) is True
    assert json.loads(capsys.readouterr().err) == {"status": "ok"}
    assert agent.log.kinds() == [event, event]


def test_release_audit_rejects_extra_arguments(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(audit_mod, "build_release_manifest", never)
    assert _handle_release_audit("now", agent, tmp_path) is True
    assert "Usage: :release-audit [--json]" in capsys.readouterr().err


def test_release_audit_prints_both_modes_and_logs(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        audit_mod,
        "build_release_manifest",
        lambda ws: SimpleNamespace(report=lambda: FakeReport({"clean": True}, "RELEASE")),
    )

    assert _handle_release_audit("", agent, tmp_path) is True
    assert "RELEASE" in capsys.readouterr().err

    assert _handle_release_audit("--json", agent, tmp_path) is True
    assert json.loads(capsys.readouterr().err) == {"clean": True}
    assert agent.log.kinds() == ["release_hygiene", "release_hygiene"]


# ── :team-plan ───────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--json", "Usage: :team-plan <goal>"),
        ("build a thing --limit", "Usage: --limit requires a number"),
        ("build a thing --limit five", "Usage: --limit requires a number"),
    ],
)
def test_team_plan_usage_errors_never_plan(rest, expected, agent, capsys, monkeypatch):
    monkeypatch.setattr(team_mod, "TeamPlanner", never)
    assert _handle_team_plan(rest, agent) is True
    assert expected in capsys.readouterr().err


def test_team_plan_turns_a_planner_refusal_into_usage_text(agent, capsys, monkeypatch):
    def _refuse():
        return SimpleNamespace(
            plan=lambda goal, limit: (_ for _ in ()).throw(ValueError("goal too vague"))
        )

    monkeypatch.setattr(team_mod, "TeamPlanner", _refuse)
    assert _handle_team_plan("do stuff", agent) is True
    assert "Usage: goal too vague" in capsys.readouterr().err


def test_team_plan_prints_both_modes(agent, capsys, monkeypatch):
    monkeypatch.setattr(
        team_mod,
        "TeamPlanner",
        lambda: SimpleNamespace(plan=lambda goal, limit: FakeReport({"roles": []}, "TEAM-PLAN")),
    )

    assert _handle_team_plan("ship the feature --limit 2", agent) is True
    assert "TEAM-PLAN" in capsys.readouterr().err

    assert _handle_team_plan("ship the feature --json", agent) is True
    assert json.loads(capsys.readouterr().err) == {"roles": []}


# ── :team-run ────────────────────────────────────────────────────────────────

@pytest.mark.parametrize(
    "rest,expected",
    [
        ("--json", "Usage: :team-run <goal>"),
        ("goal --limit", "Usage: --limit requires a number"),
        ("goal --limit x", "Usage: --limit requires a number"),
        ("goal --max-model-calls", "Usage: --max-model-calls requires a number"),
        ("goal --max-model-calls x", "Usage: --max-model-calls requires a number"),
        ("goal --max-cost-units", "Usage: --max-cost-units requires a number"),
        ("goal --max-cost-units x", "Usage: --max-cost-units requires a number"),
    ],
)
def test_team_run_usage_errors_never_execute(rest, expected, agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(team_mod, "TeamPlanner", never)
    monkeypatch.setattr(team_mod, "TeamExecutor", never)
    assert _handle_team_run(rest, agent, tmp_path) is True
    assert expected in capsys.readouterr().err


def _fake_team(monkeypatch, captured: dict):
    monkeypatch.setattr(
        team_mod, "TeamPlanner", lambda: SimpleNamespace(plan=lambda goal, limit: "PLAN")
    )

    def _executor(*, runner):
        captured["runner"] = runner

        def _run(plan, *, dry_run, budget):
            captured["dry_run"] = dry_run
            captured["budget"] = budget
            return FakeReport({"steps": []}, "TEAM-RUN")

        return SimpleNamespace(run=_run)

    monkeypatch.setattr(team_mod, "TeamExecutor", _executor)


def test_team_run_defaults_to_dry_run_without_a_subagent_runner(
    agent, tmp_path, capsys, monkeypatch
):
    captured: dict = {}
    _fake_team(monkeypatch, captured)

    assert _handle_team_run("ship it", agent, tmp_path) is True

    assert captured["dry_run"] is True
    assert captured["runner"] is None, "a dry run must not build a subagent runner"
    assert captured["budget"].max_model_calls == 10
    assert captured["budget"].max_cost_units == 20
    assert agent.log.kinds() == ["team_execution_dry_run"]
    assert "TEAM-RUN" in capsys.readouterr().err


def test_allow_effects_builds_a_runner_and_logs_a_real_execution(
    agent, tmp_path, capsys, monkeypatch
):
    import core.subagent_runner as runner_mod

    captured: dict = {}
    _fake_team(monkeypatch, captured)
    monkeypatch.setattr(runner_mod, "SubAgentRunner", SimpleNamespace)
    agent.policy = "P"
    agent.model_router = "R"
    agent.registry = "REG"

    assert _handle_team_run(
        "ship it --allow-effects --max-model-calls 3 --max-cost-units 4 --json", agent, tmp_path
    ) is True

    assert captured["dry_run"] is False
    assert captured["runner"] is not None
    assert captured["runner"].policy == "P"
    assert captured["budget"].max_model_calls == 3
    assert captured["budget"].max_cost_units == 4
    assert agent.log.kinds() == ["team_execution"]
    assert json.loads(capsys.readouterr().err) == {"steps": []}


def test_explicit_dry_run_flag_keeps_effects_off(agent, tmp_path, capsys, monkeypatch):
    """`--dry-run` after `--allow-effects` must win — the safer flag is last read."""
    captured: dict = {}
    _fake_team(monkeypatch, captured)
    monkeypatch.setattr(
        team_mod, "TeamPlanner", lambda: SimpleNamespace(plan=lambda goal, limit: captured.setdefault("limit", limit))
    )

    assert _handle_team_run("ship it --allow-effects --dry-run --limit 7", agent, tmp_path) is True

    assert captured["dry_run"] is True
    assert captured["runner"] is None
    assert captured["limit"] == 7, "a numeric --limit reaches the planner"
    assert agent.log.kinds() == ["team_execution_dry_run"]


def test_team_run_turns_a_planner_refusal_into_usage_text(agent, tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(
        team_mod,
        "TeamPlanner",
        lambda: SimpleNamespace(
            plan=lambda goal, limit: (_ for _ in ()).throw(ValueError("unknown role"))
        ),
    )
    monkeypatch.setattr(team_mod, "TeamExecutor", never)

    assert _handle_team_run("ship it", agent, tmp_path) is True
    assert "Usage: unknown role" in capsys.readouterr().err
    assert agent.log.kinds() == []


# ── :assumptions ─────────────────────────────────────────────────────────────

def _assumption(text="tests live under tests/", *, run_id="run_current", verified=None, conf=0.8):
    return SimpleNamespace(
        category="convention",
        text=text,
        confidence=conf,
        verified=verified,
        run_id=run_id,
        to_dict=lambda: {"text": text, "run_id": run_id},
    )


def test_assumptions_without_a_store(agent, capsys):
    assert _handle_assumptions("", agent) is True
    assert "assumption store not enabled" in capsys.readouterr().err


def test_assumptions_reports_a_store_error(agent, capsys):
    def _boom(_n):
        raise OSError("file gone")

    agent.assumption_store = SimpleNamespace(load_recent=_boom)
    assert _handle_assumptions("", agent) is True
    assert "assumption store error: file gone" in capsys.readouterr().err


def test_assumptions_empty_store(agent, capsys):
    agent.assumption_store = SimpleNamespace(load_recent=lambda n: [])
    assert _handle_assumptions("", agent) is True
    assert "(no assumptions recorded yet)" in capsys.readouterr().err


def test_assumptions_json_goes_to_stdout(agent, capsys):
    agent.assumption_store = SimpleNamespace(load_recent=lambda n: [_assumption()])
    assert _handle_assumptions("--json", agent) is True

    captured = capsys.readouterr()
    assert json.loads(captured.out)[0]["text"] == "tests live under tests/"
    assert captured.err == "", "JSON mode is machine output — stdout only"


def test_assumptions_marks_the_current_run_and_the_verification_state(agent, capsys):
    agent.assumption_store = SimpleNamespace(
        load_recent=lambda n: [
            _assumption("from this run", run_id="run_current", verified=True),
            _assumption("from an older run", run_id="run_abcdefgh12345678", verified=False),
            _assumption("unchecked", run_id="run_current", verified=None, conf=0.5),
        ]
    )

    assert _handle_assumptions("", agent) is True

    err = capsys.readouterr().err
    assert "[current]" in err
    assert "[run …12345678]" in err, "an older run is shown by the last 8 chars of its id"
    assert "run_abcdefgh12345678" not in err, "the full run id is not printed"
    assert "✓" in err and "✗" in err
    assert "(50%)" in err
