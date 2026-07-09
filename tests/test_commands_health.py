from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import cli.commands_health as health
from main import handle_meta_command


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    text = "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    path.write_text(text, encoding="utf-8")


class _BombPlanner:
    def __init__(self) -> None:
        self.calls = 0

    def plan(self, *args, **kwargs):  # pragma: no cover - must never run
        self.calls += 1
        raise AssertionError("planner must not be called")


class _BombLLM:
    def __init__(self) -> None:
        self.calls: list[dict] = []

    def complete(self, *args, **kwargs):  # pragma: no cover - must never run
        self.calls.append({"args": args, "kwargs": kwargs})
        raise AssertionError("model must not be called")


_TD_011_012_TITLE = (
    "TD-011 / TD-012 \u2014 Live Model Discovery + Provider Catalog Refresh "
    "(read-only)"
)

_SELF_BUILD_PROPOSAL = """\
# TD-038 slice 2 - proposal: self-build grounded target coverage (docs only)

### D. Mapper coverage for a docs-only / operator-guide target **first**

- **What:** first grounded target is `docs/self_build.md` (already in
  `DEFAULT_CANDIDATE_TARGETS`).
"""


def _write_model_discovery_mapping_evidence(workspace: Path) -> None:
    (workspace / "TECH_DEBT.md").write_text(
        f"{_TD_011_012_TITLE}\n"
        "Статус: Partial — dry-run/read-only foundation.\n",
        encoding="utf-8",
    )
    (workspace / "core").mkdir()
    (workspace / "core" / "model_discovery.py").write_text(
        '"""Live Model Discovery + Provider Catalog diff -- read-only / dry-run (TD-011/012).\n'
        "Uses build_discovery_audit and build_discovery_report.\n"
        "It NEVER writes the catalog.\n"
        '"""\n',
        encoding="utf-8",
    )
    (workspace / "tests").mkdir()
    (workspace / "tests" / "test_model_discovery.py").write_text(
        '"""Tests for TD-011/012 read-only Live Model Discovery + catalog diff.\n'
        "The discovery never writes and leaks no secret values.\n"
        '"""\n'
        "from core.model_discovery import build_discovery_audit, build_discovery_report\n",
        encoding="utf-8",
    )


def _write_self_build_docs_pilot_signal(workspace: Path) -> None:
    proposal = (
        workspace
        / "docs"
        / "proposals"
        / "self-build-grounded-target-coverage-proposal.md"
    )
    proposal.parent.mkdir(parents=True)
    proposal.write_text(_SELF_BUILD_PROPOSAL, encoding="utf-8")


def test_dry_health_pass_is_handled_locally_without_planner_or_model(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    monkeypatch.setattr(
        health,
        "_git_tree_payload",
        lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )
    planner = _BombPlanner()
    llm = _BombLLM()
    agent = SimpleNamespace(planner=planner, llm=llm)

    assert handle_meta_command(":dry-health-pass", agent, workspace) is True

    out = capsys.readouterr()
    assert "=== dry health pass ===" in out.err
    assert planner.calls == 0
    assert llm.calls == []


def test_dry_health_pass_json_reports_collected_facts(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys,
):
    now = datetime.now(timezone.utc)
    _write_json(
        workspace / "data" / "daemon_heartbeat.json",
        {"ts": now.isoformat(), "event": "tick_complete"},
    )
    _write_jsonl(
        workspace / "data" / "approval_inbox.jsonl",
        [
            {"id": "ain_1", "status": "pending"},
            {"id": "ain_2", "status": "approved"},
        ],
    )
    _write_jsonl(
        workspace / "data" / "runtime_tasks.jsonl",
        [
            {"id": "rtask_1", "status": "paused"},
            {"id": "rtask_2", "status": "pending"},
        ],
    )
    _write_jsonl(
        workspace / "data" / "runtime_schedules.jsonl",
        [
            {
                "id": "sched_1",
                "status": "active",
                "next_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
            {
                "id": "sched_2",
                "status": "active",
                "next_run_at": (now + timedelta(days=1)).isoformat(),
            },
        ],
    )
    _write_json(
        workspace / "config" / "budget_limits.json",
        {
            "windows": {
                "hour": {"llm_calls": 10, "model_cost_units": 20},
                "day": {"llm_calls": 100, "model_cost_units": 200},
            }
        },
    )
    _write_jsonl(
        workspace / "data" / "budget_ledger.jsonl",
        [
            {
                "counter": "llm_calls",
                "amount": 3,
                "created_at": now.isoformat(),
            }
        ],
    )
    _write_json(
        workspace / "data" / "self_build_producer_state.json",
        {"last_proposed_at": (now - timedelta(days=2)).isoformat()},
    )
    _write_jsonl(
        workspace / "data" / "value_reviews.jsonl",
        [
            {"item_id": "ain_1", "verdict": "accepted"},
            {"item_id": "ain_1", "verdict": "rejected_low_value"},
            {"item_id": "ain_2", "verdict": "rejected_risky"},
        ],
    )
    (workspace / "TECH_DEBT.md").write_text(
        "TD-999 - ignored shape\n\n"
        "TD-999 — Open health follow-up\n"
        "Статус: Partial — still open.\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        health,
        "_git_tree_payload",
        lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    agent = SimpleNamespace(planner=_BombPlanner(), llm=_BombLLM())
    assert handle_meta_command(":dry-health-pass --json", agent, workspace) is True

    payload = json.loads(capsys.readouterr().err)
    assert payload["daemon"]["status"] == "alive"
    assert payload["approvals"]["pending"] == 1
    assert payload["tasks"]["paused"] == 1
    assert payload["scheduler"]["due"] == 1
    assert payload["budget"]["hour"]["counters"]["llm_calls"] == {"used": 3, "limit": 10}
    assert payload["budget"]["day"]["counters"]["llm_calls"] == {"used": 3, "limit": 100}
    assert payload["kill_switch"]["active"] is False
    assert payload["self_build"]["cooldown"]["status"] == "ready"
    assert payload["self_build"]["grounded_target"]["classification"] == "off_allowlist"
    assert payload["value_reviews"]["counts"] == {
        "accepted": 0,
        "rejected_low_value": 1,
        "rejected_misleading_summary": 0,
        "rejected_risky": 1,
        "rejected_wrong_target": 0,
    }
    assert payload["git"]["status"] == "clean"
    assert payload["next_safe_action"] == health.NEXT_APPROVALS


def test_grounded_target_payload_reports_mapped_td_011_012(workspace: Path):
    _write_model_discovery_mapping_evidence(workspace)

    payload = health._grounded_target_payload(workspace)

    assert payload["classification"] == "mapped"
    assert payload["target_path"] == "core/model_discovery.py"
    assert payload["source_target_path"] == "TD-011 / TD-012"
    assert payload["mapping_decision"] == "mapped"
    assert payload["mapping_rule"] == "td_011_012_model_discovery"


def test_grounded_target_payload_reports_docs_pilot_available(workspace: Path):
    _write_self_build_docs_pilot_signal(workspace)

    payload = health._grounded_target_payload(workspace)

    assert payload["classification"] == "available"
    assert payload["target_path"] == "docs/self_build.md"
    assert payload["source_target_path"] == "docs/self_build.md"
    assert payload["mapping_decision"] == "concrete"
    assert payload["mapping_rule"] == "concrete"


def test_grounded_target_payload_docs_pilot_not_available_after_doc_exists(
    workspace: Path,
):
    _write_self_build_docs_pilot_signal(workspace)
    (workspace / "docs" / "self_build.md").write_text(
        "# Self-build\n\nAlready created.\n",
        encoding="utf-8",
    )

    payload = health._grounded_target_payload(workspace)

    assert payload["classification"] == "none"
    assert payload["target_path"] is None


def test_dry_health_pass_missing_files_are_unknown_and_do_not_create_state(
    workspace: Path,
):
    assert not (workspace / "data").exists()

    payload = health.collect_dry_health_pass(
        workspace,
        git_status_fn=lambda _workspace: {"status": "unknown", "reason": "not a repo"},
    )

    assert payload["daemon"]["status"] == "unknown"
    assert payload["approvals"]["pending"] is None
    assert payload["tasks"]["paused"] is None
    assert payload["scheduler"]["due"] is None
    assert payload["budget"]["status"] == "unknown"
    assert payload["kill_switch"]["active"] is None
    assert payload["self_build"]["cooldown"]["status"] == "unknown"
    assert payload["self_build"]["grounded_target"]["classification"] == "unknown"
    assert payload["value_reviews"]["status"] == "unknown"
    assert not (workspace / "data").exists()


def test_dry_health_pass_broken_files_are_unknown(workspace: Path):
    (workspace / "data").mkdir()
    (workspace / "data" / "approval_inbox.jsonl").write_text("{not-json}\n", encoding="utf-8")

    payload = health.collect_dry_health_pass(
        workspace,
        git_status_fn=lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    assert payload["approvals"]["status"] == "unknown"
    assert payload["approvals"]["pending"] is None


def test_next_safe_action_is_deterministic_ordered_rules():
    base = {
        "kill_switch": {"active": False},
        "git": {"status": "clean"},
        "approvals": {"pending": 0},
        "daemon": {"status": "alive"},
        "self_build": {"grounded_target": {"classification": "none"}},
        "tasks": {"paused": 0},
    }

    payload = {**base, "kill_switch": {"active": True}, "git": {"status": "dirty"}}
    assert health._select_next_safe_action(payload) == health.NEXT_KILL_SWITCH

    payload = {**base, "git": {"status": "dirty"}}
    assert health._select_next_safe_action(payload) == health.NEXT_GIT_DIRTY

    payload = {**base, "approvals": {"pending": 2}}
    assert health._select_next_safe_action(payload) == health.NEXT_APPROVALS

    payload = {**base, "daemon": {"status": "stale"}}
    assert health._select_next_safe_action(payload) == health.NEXT_DAEMON

    payload = {
        **base,
        "daemon": {
            "status": "stale",
            "interpretation": "expected_without_scheduled_daemon",
        },
        "scheduler": {"due": 0, "status": "known"},
    }
    assert health._select_next_safe_action(payload) == health.NEXT_DAEMON_STALE_MANUAL

    payload = {
        **base,
        "daemon": {
            "status": "stale",
            "interpretation": "stale_scheduled_daemon_expected",
        },
        "scheduler": {"due": 1, "status": "known"},
    }
    assert health._select_next_safe_action(payload) == health.NEXT_DAEMON

    payload = {
        **base,
        "self_build": {"grounded_target": {"classification": "off_allowlist"}},
    }
    assert health._select_next_safe_action(payload) == health.NEXT_GROUNDED

    payload = {**base, "tasks": {"paused": 1}}
    assert health._select_next_safe_action(payload) == health.NEXT_PAUSED

    assert health._select_next_safe_action(base) == health.NEXT_DRY_RUN


def test_stale_daemon_manual_mode_reports_interpretation(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
):
    import agent_tick

    now = datetime.now(timezone.utc)
    old_tick = now - timedelta(hours=2)
    _write_json(
        workspace / "data" / "daemon_heartbeat.json",
        {"ts": old_tick.isoformat(), "event": "tick_complete"},
    )
    _write_jsonl(
        workspace / "data" / "runtime_schedules.jsonl",
        [
            {
                "id": "sched_1",
                "status": "active",
                "next_run_at": (now + timedelta(days=1)).isoformat(),
            },
        ],
    )
    monkeypatch.setattr(
        health,
        "_git_tree_payload",
        lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    payload = health.collect_dry_health_pass(
        workspace,
        now=now,
        git_status_fn=lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    daemon = payload["daemon"]
    assert daemon["status"] == "stale"
    assert daemon["interpretation"] == "expected_without_scheduled_daemon"
    assert daemon["staleness_threshold_seconds"] == (
        agent_tick.EXPECTED_TICK_INTERVAL_SECONDS * agent_tick.STALENESS_FACTOR
    )
    assert daemon["last_tick_at"] == old_tick.isoformat()
    assert payload["next_safe_action"] == health.NEXT_DAEMON_STALE_MANUAL


def test_stale_daemon_with_due_scheduler_still_blocks(workspace: Path, monkeypatch: pytest.MonkeyPatch):
    now = datetime.now(timezone.utc)
    _write_json(
        workspace / "data" / "daemon_heartbeat.json",
        {"ts": (now - timedelta(hours=2)).isoformat(), "event": "tick_complete"},
    )
    _write_jsonl(
        workspace / "data" / "runtime_schedules.jsonl",
        [
            {
                "id": "sched_1",
                "status": "active",
                "next_run_at": (now - timedelta(minutes=1)).isoformat(),
            },
        ],
    )
    monkeypatch.setattr(
        health,
        "_git_tree_payload",
        lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    payload = health.collect_dry_health_pass(
        workspace,
        now=now,
        git_status_fn=lambda _workspace: {"status": "clean", "porcelain_count": 0},
    )

    assert payload["daemon"]["interpretation"] == "stale_scheduled_daemon_expected"
    assert payload["next_safe_action"] == health.NEXT_DAEMON
