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
