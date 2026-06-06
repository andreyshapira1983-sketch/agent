"""Tests for the paced-campaign daemon mode (`agent_tick.run_paced_campaign`).

This is the step-3b wiring: the campaign engine can pace itself over real
wall-clock time (cycle_pause_seconds / max_wall_clock_seconds), but during a
multi-hour unattended run the operator must still be able to SEE that the agent
is alive. ``run_paced_campaign`` drives the campaign loop in a single daemon
process and emits a heartbeat PER CYCLE so ``--status`` stays meaningful
throughout.

The heavy collaborators (heartbeat writer, the campaign loop, the agent builder)
are injected here as deterministic fakes — no LLM, no real agent, no real sleep.
"""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

from agent_tick import run_paced_campaign


class _HBRecorder:
    """Captures every heartbeat payload the daemon writes."""

    def __init__(self) -> None:
        self.calls: list[dict] = []

    def __call__(self, workspace: Path, payload: dict) -> None:
        self.calls.append(payload)

    @property
    def events(self) -> list[str]:
        return [c.get("event") for c in self.calls]


def _fake_result(cycles: int = 3):
    return SimpleNamespace(
        status="completed",
        stop_reason="",
        cycles_run=cycles,
        totals={"llm_calls": cycles, "cost_units": 0, "wall_clock_seconds": 0.0},
        user_summary=lambda: "=== fake campaign ===",
    )


def _make_run_campaign(cycles: int = 3, *, record_config: list | None = None):
    """A fake run_campaign that drives on_cycle `cycles` times then returns."""

    def _run(config, *, agent, workspace, approval_inbox, ledger, on_cycle):
        if record_config is not None:
            record_config.append(config)
        for i in range(1, cycles + 1):
            on_cycle({
                "cycle": i,
                "result": "completed",
                "idle": False,
                "llm_calls": i,
                "cost_units": 0,
                "useful_cycles": i,
                "idle_cycles": 0,
                "repeat_cycles": 0,
            })
        return _fake_result(cycles)

    return _run


def test_emits_a_heartbeat_per_cycle(workspace: Path):
    hb = _HBRecorder()
    rc = run_paced_campaign(
        workspace,
        dry_run=True,
        max_cycles=3,
        heartbeat_fn=hb,
        run_campaign_fn=_make_run_campaign(3),
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    assert rc == 0
    # start + 3 per-cycle + complete
    assert hb.events[0] == "campaign_start"
    assert hb.events.count("campaign_cycle") == 3
    assert hb.events[-1] == "campaign_complete"
    # every cycle heartbeat carries honest mode/effect liveness fields
    cycle_beats = [c for c in hb.calls if c["event"] == "campaign_cycle"]
    assert [c["cycle"] for c in cycle_beats] == [1, 2, 3]
    assert all(c["mode"] == "dry_run" and c["effects"] == "disabled"
               for c in cycle_beats)


def test_dry_run_flag_propagates_to_config(workspace: Path):
    seen_config: list = []
    hb = _HBRecorder()
    run_paced_campaign(
        workspace,
        dry_run=True,
        goal="stabilise daemon",
        max_cycles=2,
        cycle_pause_seconds=7,
        max_wall_clock_seconds=600,
        heartbeat_fn=hb,
        run_campaign_fn=_make_run_campaign(2, record_config=seen_config),
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    cfg = seen_config[0]
    assert cfg.dry_run is True
    assert cfg.goal == "stabilise daemon"
    assert cfg.cycle_pause_seconds == 7
    assert cfg.max_wall_clock_seconds == 600


def test_live_mode_marks_heartbeats_live(workspace: Path):
    hb = _HBRecorder()
    run_paced_campaign(
        workspace,
        dry_run=False,
        max_cycles=1,
        heartbeat_fn=hb,
        run_campaign_fn=_make_run_campaign(1),
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    cycle_beats = [c for c in hb.calls if c["event"] == "campaign_cycle"]
    assert all(c["mode"] == "live" and c["effects"] == "enabled"
               for c in cycle_beats)


def test_invalid_config_returns_error_before_any_heartbeat(workspace: Path):
    hb = _HBRecorder()
    rc = run_paced_campaign(
        workspace,
        max_cycles=0,  # invalid -> CampaignConfig raises ValueError
        heartbeat_fn=hb,
        run_campaign_fn=_make_run_campaign(3),
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    assert rc == 1
    # The config error happens before the campaign even starts.
    assert hb.calls == []


def test_campaign_exception_returns_error_and_writes_error_heartbeat(workspace: Path):
    hb = _HBRecorder()

    def _boom(config, *, agent, workspace, approval_inbox, ledger, on_cycle):
        raise RuntimeError("planner exploded")

    rc = run_paced_campaign(
        workspace,
        max_cycles=2,
        heartbeat_fn=hb,
        run_campaign_fn=_boom,
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    assert rc == 1
    assert hb.events[0] == "campaign_start"
    assert hb.events[-1] == "campaign_error"
    assert "planner exploded" in hb.calls[-1]["error"]


def test_per_cycle_heartbeat_failure_never_kills_the_run(workspace: Path):
    # A transient heartbeat-write failure during a long run must not abort the
    # campaign — the per-cycle write is best-effort.
    calls = {"n": 0}

    def _flaky_hb(ws: Path, payload: dict) -> None:
        calls["n"] += 1
        if payload.get("event") == "campaign_cycle":
            raise OSError("disk full")

    rc = run_paced_campaign(
        workspace,
        max_cycles=3,
        heartbeat_fn=_flaky_hb,
        run_campaign_fn=_make_run_campaign(3),
        build_agent_fn=lambda ws: SimpleNamespace(log=None),
    )
    assert rc == 0  # survived all 3 failing per-cycle writes
