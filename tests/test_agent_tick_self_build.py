"""Tests for the autonomous self-build producer wiring in the daemon (TD-026).

These pin the ONE new gate agent_tick owns — a persistent cooldown that runs
*before* build_agent — plus the status/heartbeat surface and the hard safety
invariants: at most one producer call per tick, at most one approval item,
cooldown spent only on ``status="proposed"``, and no self-apply-lane / git
side effects. Everything is driven with fakes (no real LLM/provider/network/git).
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent_tick
from agent_tick import (
    _cooldown_remaining_seconds,
    _maybe_produce_self_build,
    _read_producer_state,
    _self_build_cooldown_hours,
    _write_producer_state,
)


# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeInbox:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return SimpleNamespace(id="ain-fake")


class _FakeReport:
    """Mirrors ProducerReport.to_dict() for the fields the wrapper reads."""

    def __init__(self, status: str, *, approval_id=None, target_path=None,
                 next_human_action: str = "") -> None:
        self._d = {
            "status": status,
            "approval_id": approval_id,
            "target_path": target_path,
            "next_human_action": next_human_action,
        }

    def to_dict(self) -> dict:
        return dict(self._d)


class _SpyProducer:
    """Records how many times it was called and returns a canned report."""

    def __init__(self, report: _FakeReport) -> None:
        self.report = report
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return self.report


class _SpyBuildAgent:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, workspace):
        self.calls += 1
        return SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda role: object())
        )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── cooldown helpers ────────────────────────────────────────────────────────


def test_cooldown_zero_when_no_state():
    assert _cooldown_remaining_seconds({}, cooldown_hours=12.0) == 0.0


def test_cooldown_remaining_positive_within_window():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = {"last_proposed_at": _iso(now - timedelta(hours=1))}
    remaining = _cooldown_remaining_seconds(state, cooldown_hours=12.0, now=now)
    assert 10 * 3600 < remaining <= 11 * 3600


def test_cooldown_zero_after_window():
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    state = {"last_proposed_at": _iso(now - timedelta(hours=13))}
    assert _cooldown_remaining_seconds(state, cooldown_hours=12.0, now=now) == 0.0


def test_cooldown_unparseable_timestamp_never_blocks():
    state = {"last_proposed_at": "not-a-date"}
    assert _cooldown_remaining_seconds(state, cooldown_hours=12.0) == 0.0


def test_cooldown_hours_env_override(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.delenv("AGENT_SELF_BUILD_COOLDOWN_HOURS", raising=False)
    assert _self_build_cooldown_hours() == 12.0
    monkeypatch.setenv("AGENT_SELF_BUILD_COOLDOWN_HOURS", "6")
    assert _self_build_cooldown_hours() == 6.0
    monkeypatch.setenv("AGENT_SELF_BUILD_COOLDOWN_HOURS", "junk")
    assert _self_build_cooldown_hours() == 12.0


def test_state_roundtrip(tmp_path: Path):
    _write_producer_state(tmp_path, "2026-07-01T12:00:00+00:00")
    assert _read_producer_state(tmp_path)["last_proposed_at"] == "2026-07-01T12:00:00+00:00"


# ── cooldown gate runs before build_agent ────────────────────────────────────


def test_cooldown_wait_does_not_build_agent_or_call_producer(tmp_path: Path):
    now = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    _write_producer_state(tmp_path, _iso(now - timedelta(hours=1)))
    builder = _SpyBuildAgent()
    producer = _SpyProducer(_FakeReport("proposed"))

    out = _maybe_produce_self_build(
        tmp_path, _FakeInbox(),
        now_iso=_iso(now), cooldown_hours=12.0,
        build_agent_fn=builder, producer_fn=producer,
    )
    assert out["self_build_status"] == "cooldown_wait"
    assert builder.calls == 0  # agent never built during cooldown
    assert producer.calls == 0


# ── producer call / cooldown accounting ──────────────────────────────────────


def _run(tmp_path, report, *, now=None, cooldown_hours=12.0, inbox=None):
    now = now or datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    builder = _SpyBuildAgent()
    producer = _SpyProducer(report)
    out = _maybe_produce_self_build(
        tmp_path, inbox or _FakeInbox(),
        now_iso=_iso(now), cooldown_hours=cooldown_hours,
        build_agent_fn=builder, producer_fn=producer,
    )
    return out, builder, producer


def test_proposed_writes_cooldown_and_surfaces_fields(tmp_path: Path):
    report = _FakeReport(
        "proposed", approval_id="ain-1", target_path="core/redaction.py",
        next_human_action="run :self-apply-run ain-1",
    )
    out, builder, producer = _run(tmp_path, report)
    assert out["self_build_status"] == "proposed"
    assert out["approval_id"] == "ain-1"
    assert out["next_human_action"] == "run :self-apply-run ain-1"
    assert builder.calls == 1
    assert producer.calls == 1  # at most one producer call per tick
    # cooldown now recorded
    assert _read_producer_state(tmp_path)["last_proposed_at"]


@pytest.mark.parametrize(
    "status",
    ["no_patch", "critic_veto", "budget_wait", "budget_kill_switch",
     "approval_wait", "dirty_tree_wait"],
)
def test_non_proposed_status_does_not_spend_cooldown(tmp_path: Path, status: str):
    out, _, producer = _run(tmp_path, _FakeReport(status))
    assert out["self_build_status"] == status
    assert producer.calls == 1
    assert _read_producer_state(tmp_path) == {}  # cooldown untouched


def test_second_tick_after_proposed_hits_cooldown(tmp_path: Path):
    t0 = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    out1, _, _ = _run(tmp_path, _FakeReport("proposed", approval_id="ain-1"), now=t0)
    assert out1["self_build_status"] == "proposed"
    # 2 hours later, well within the 12h window -> cooldown, no producer call
    out2, builder2, producer2 = _run(
        tmp_path, _FakeReport("proposed"), now=t0 + timedelta(hours=2)
    )
    assert out2["self_build_status"] == "cooldown_wait"
    assert builder2.calls == 0
    assert producer2.calls == 0


def test_exception_becomes_error_and_does_not_raise(tmp_path: Path):
    def _boom(workspace):
        raise RuntimeError("build blew up")

    out = _maybe_produce_self_build(
        tmp_path, _FakeInbox(),
        now_iso="2026-07-01T12:00:00+00:00", cooldown_hours=12.0,
        build_agent_fn=_boom, producer_fn=_SpyProducer(_FakeReport("proposed")),
    )
    assert out["self_build_status"] == "error"
    assert "build blew up" in out["error"]
    assert _read_producer_state(tmp_path) == {}  # error never spends cooldown


def test_error_status_from_producer_does_not_spend_cooldown(tmp_path: Path):
    out, _, _ = _run(tmp_path, _FakeReport("error"))
    assert out["self_build_status"] == "error"
    assert _read_producer_state(tmp_path) == {}


# ── hard safety: no lane execution / no git side effects ─────────────────────


def test_wrapper_never_imports_or_runs_self_apply_lane_helpers():
    # The wrapper must NOT reference run_self_apply_lane anywhere in its source.
    src = Path(agent_tick.__file__).read_text(encoding="utf-8")
    # Grab just the wrapper function body.
    start = src.index("def _maybe_produce_self_build(")
    end = src.index("# ── main tick", start)
    body = src[start:end]
    assert "run_self_apply_lane" not in body
    # Look for actual method-call syntax, not prose in the docstring.
    for banned in (".commit(", ".push(", ".fetch(", ".pull(", ".merge(",
                   ".reset_hard(", ".stage_all(", ".create_temp_branch("):
        assert banned not in body, banned


def test_real_producer_creates_at_most_one_item_and_no_git(tmp_path: Path):
    # Integration-ish: use the REAL producer with a fake LLM whose Manager
    # selects no target -> no_patch, no item, no git touched.
    from core.self_build_producer import produce_self_apply_proposal

    class _FakeLLM:
        def complete(self, *, system, user, max_tokens=2000, temperature=0.0):
            return json.dumps({"target": None, "diagnosis": "nothing"})

    class _FakeVCS:
        def __init__(self):
            self.mutations = []

        def is_clean(self):
            return True

    from core.approval_inbox import ApprovalInbox

    inbox = ApprovalInbox(path=None)
    vcs = _FakeVCS()

    def _build(ws):
        return SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda role: _FakeLLM())
        )

    out = _maybe_produce_self_build(
        tmp_path, inbox,
        now_iso="2026-07-01T12:00:00+00:00", cooldown_hours=12.0,
        build_agent_fn=_build, producer_fn=produce_self_apply_proposal,
        vcs=vcs, kill_switch=SimpleNamespace(active=False, reason=""),
        budget_snapshot={"windows": []},
    )
    assert out["self_build_status"] == "no_patch"
    assert inbox.list() == []
    assert vcs.mutations == []


def test_config_budget_limits_never_written(tmp_path: Path):
    _run(tmp_path, _FakeReport("proposed", approval_id="ain-1"))
    assert not (tmp_path / "config" / "budget_limits.json").exists()
