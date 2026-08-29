"""The marathon breathes: pace_seconds spreads cycles over the shift.

Born 2026-08-29 from the first real marathon attempt: with no pacing the
"10-hour" shift sprinted back-to-back ~40s cycles into the hourly model-cost
wall (3003/3000 units) and died at cycle 27 after 17 minutes.  A shift that
must last N hours needs a heartbeat, not a sprint: after each cycle the
session waits until the cycle's slot of ``pace_seconds`` is spent, so burn
rate stays under the persistent budget windows and wall-clock matches the
operator's word ("работать 10-12 часов").

Contract under test:
- ``pace_seconds=0`` (default) keeps the old sprint behaviour — no sleeps;
- with pacing, the session sleeps the *remainder* of the slot (pace minus
  the cycle's own duration), never after the final cycle;
- the pause never oversleeps the session deadline;
- a negative pace is a config error, loudly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

import core.work_session as ws_mod
from core.work_session import WorkSessionConfig, run_work_session
from tests.test_work_session import _agent


class _PacedClock:
    """Controllable ``time`` stand-in recording sleeps and advancing on them."""

    def __init__(self, start: float = 0.0) -> None:
        self.now = start
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


def _run_with_clock(
    workspace: Path,
    monkeypatch: pytest.MonkeyPatch,
    config: WorkSessionConfig,
    *,
    cycle_cost_s: float = 40.0,
) -> tuple[_PacedClock, ws_mod.WorkSessionResult]:
    clock = _PacedClock()
    monkeypatch.setattr(ws_mod, "time", clock)
    real_run = ws_mod.AutonomousRuntime.run

    def timed_run(self, cfg):
        clock.now += cycle_cost_s
        return real_run(self, cfg)

    monkeypatch.setattr(ws_mod.AutonomousRuntime, "run", timed_run)
    agent = _agent(workspace)
    result = run_work_session(config, agent=agent, workspace=workspace)
    return clock, result


class TestPaceConfig:
    def test_default_is_zero(self):
        assert WorkSessionConfig().pace_seconds == 0.0

    def test_negative_pace_raises(self):
        with pytest.raises(ValueError):
            WorkSessionConfig(pace_seconds=-1.0)


class TestPacedSession:
    def test_zero_pace_never_sleeps(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        clock, result = _run_with_clock(
            workspace,
            monkeypatch,
            WorkSessionConfig(goal="test", dry_run=True, minutes=60.0, max_cycles=3, pace_seconds=0.0),
        )
        assert result.cycles_run == 3
        assert clock.sleeps == []

    def test_sleeps_the_slot_remainder_between_cycles(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        clock, result = _run_with_clock(
            workspace,
            monkeypatch,
            WorkSessionConfig(goal="test", dry_run=True, minutes=60.0, max_cycles=3, pace_seconds=300.0),
            cycle_cost_s=40.0,
        )
        assert result.cycles_run == 3
        # A pause follows cycles 1 and 2 only: the final cycle owes nothing.
        assert clock.sleeps == [pytest.approx(260.0), pytest.approx(260.0)]

    def test_slow_cycle_longer_than_slot_skips_the_pause(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        clock, result = _run_with_clock(
            workspace,
            monkeypatch,
            WorkSessionConfig(goal="test", dry_run=True, minutes=60.0, max_cycles=2, pace_seconds=30.0),
            cycle_cost_s=45.0,
        )
        assert result.cycles_run == 2
        assert clock.sleeps == []

    def test_pause_never_oversleeps_the_deadline(
        self, workspace: Path, monkeypatch: pytest.MonkeyPatch
    ):
        # Deadline 2 min; cycle costs 40s, slot is 600s.  The pause after
        # cycle 1 must be clamped to the 80s left, and the next loop pass
        # stops on time_budget instead of running a starved extra cycle.
        clock, result = _run_with_clock(
            workspace,
            monkeypatch,
            WorkSessionConfig(goal="test", dry_run=True, minutes=2.0, max_cycles=5, pace_seconds=600.0),
            cycle_cost_s=40.0,
        )
        assert result.cycles_run == 1
        assert result.stop_reason == "time_budget"
        assert clock.sleeps == [pytest.approx(80.0)]
