"""The exam driver waits for the turn's closing marker, never for silence.

Measured 2026-09-05 (ACTUATION_TEST_2026-09-05.md in git history, raw timeline):
the first driver ended a turn after 40 s of quiet output while the planner
was silent for 44 s, the stop that followed sent `:quit` and terminated the
process during the second planner call, and the one permitted live
provenance check died without a synthesizer call. Pinned here: a long
silent pause does not end the turn; the closing marker does; a dead process
and the timeout are the only other exits.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

_ROOT = Path(__file__).resolve().parents[1]


def _driver():
    spec = importlib.util.spec_from_file_location("exam_driver", _ROOT / "scripts" / "exam_driver.py")
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class _Clock:
    """Deterministic time: sleep advances the clock; output may arrive at a
    scheduled moment, like a planner that answers after a long pause."""

    def __init__(self, buf: list[str], arrivals: dict[float, list[str]]):
        self.now, self.buf, self.arrivals = 0.0, buf, dict(arrivals)

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        for at in sorted(self.arrivals):
            if at <= self.now:
                self.buf.extend(self.arrivals.pop(at))

    def clock(self) -> float:
        return self.now


def test_a_long_silent_planner_call_does_not_end_the_turn():
    d = _driver()
    buf = ["[RUN_] run_identity …\n", "[MODE] model_call_start role=planner …\n"]
    # 44 s of nothing, then the plan, a tool, a second planner call, the
    # synthesizer, and finally the closing marker plus the answer text.
    clock = _Clock(buf, {
        44.0: ["[MODE] model_call_end role=planner …\n", "[PLAN] planner …\n"],
        60.0: ["[MODE] model_call_end role=synthesizer …\n"],
        61.0: ["[PROC] procedural_memory_update episode_id=… status=skipped\n", "\nОтвет.\n"],
    })
    why = d.wait_for_turn_end(buf, 0, proc_alive=lambda: True, sleep=clock.sleep, clock=clock.clock, grace=2.0)
    assert why == "marker"
    assert clock.now >= 61.0, "the turn must not end during the 44-second silence"
    assert "Ответ." in "".join(buf)


def test_a_dead_process_and_the_timeout_are_the_only_other_exits():
    d = _driver()
    buf = ["[RUN_] run_identity …\n"]
    clock = _Clock(buf, {})
    alive = [True]

    def proc_alive():
        if clock.now >= 10:
            alive[0] = False
        return alive[0]

    assert d.wait_for_turn_end(buf, 0, proc_alive=proc_alive, sleep=clock.sleep, clock=clock.clock) == "exit"
    clock2 = _Clock(["x\n"], {})
    assert d.wait_for_turn_end(["x\n"], 0, proc_alive=lambda: True, timeout=30, sleep=clock2.sleep, clock=clock2.clock) == "timeout"


def test_silence_alone_never_ends_a_turn():
    d = _driver()
    buf = ["[RUN_] run_identity …\n"]
    clock = _Clock(buf, {})
    why = d.wait_for_turn_end(buf, 0, proc_alive=lambda: True, timeout=500, sleep=clock.sleep, clock=clock.clock)
    assert why == "timeout" and clock.now > 500, "500 s of silence: still not an end of turn"
