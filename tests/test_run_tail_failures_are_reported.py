"""A failure in the run tail must leave a trace.

Census item A7. Two handlers in `core/loop_run_tail.py` swallowed without a
word, and — as in A3 — the cure was already in reach: `_sensor_failed` is
declared in this file's host contract and used twice a few lines above, for the
stagnation shadow and for memory compaction. The means existed; two of the four
handlers in the same function simply did not use it.

Measured before the fix, and the two hide differently:

    user profile        healthy: logs `user_profile_update`, profile replaced
                        broken : NO event, profile unchanged

    assumption store    healthy: 2 assumptions written to the store
                        broken : 0 written, and the journal is byte-for-byte
                                 the journal of a healthy run

The second is the worse hiding place, and the reason is not the missing event.
`last_assumptions` is set from the in-memory object BEFORE the store call, so
the agent still reports the assumptions it believes it saved. Nothing
downstream can tell. A later run reads the store, finds nothing, and behaves as
though the turn never had assumptions at all.

The first is milder but the same shape: `user_profile_update` is absent both
when the update failed and when no update was due (`may_profile` false, or no
store configured). Two causes, one picture — which is exactly what MIR-077 was
closed for and what the census found still living in the layer.

What is deliberately NOT changed: neither failure aborts the turn. An answer
that is ready to go must not be lost because a profile write failed. Reporting
and failing safe are different jobs, and the old handlers did only the second.
"""
from __future__ import annotations

from types import SimpleNamespace
from typing import ClassVar

import pytest

from core.loop_run_tail import AgentLoopRunTail


class _Log:
    trace_id = "trace"
    log_dir = None

    def __init__(self) -> None:
        self.events: list[str] = []

    def log(self, name, payload=None, **kw):  # noqa: ANN001 - test spy
        self.events.append(name)


class _Profile:
    expertise = "expert"
    verbosity = "brief"
    language = "ru"
    interaction_count = 1
    interests: ClassVar[list[str]] = []
    expert_signals: ClassVar[list[str]] = []
    novice_signals: ClassVar[list[str]] = []


class _ProfileStore:
    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom

    def update_from_interaction(self, **kwargs):
        if self.boom:
            raise RuntimeError("boom")
        return _Profile()


class _AssumptionStore:
    def __init__(self, *, boom: bool = False) -> None:
        self.boom = boom
        self.saved: list[str] = []

    def save_many(self, items) -> None:
        if self.boom:
            raise RuntimeError("boom")
        self.saved.extend(items)


class _Agent(AgentLoopRunTail):
    """Only what the tail touches — no runtime needed to ask this."""

    def __init__(self, *, profile_boom: bool = False, store_boom: bool = False) -> None:
        self.log = _Log()
        self.memory = None
        self.assumption_store = _AssumptionStore(boom=store_boom)
        self.user_profile_store = _ProfileStore(boom=profile_boom)
        self.last_verification = None
        self.last_user_profile = None
        self.last_assumptions = None
        self._current_attempt = 1
        self._defect_signals: list[str] = []
        self._cycle_findings: list = []
        self.last_source_ranking = None
        self.sensor_failures: list[str] = []

    def _sensor_failed(self, sensor: str, exc: BaseException) -> None:
        self.sensor_failures.append(sensor)
        self.log.log("sensor_failed")


def _finalize(agent: _Agent):
    return agent._finalize_run_tail(
        "готовый ответ",
        user_question="вопрос",
        artifacts={},
        planner_out=SimpleNamespace(reasoning="r", sources=[]),
        replan_exhausted=False,
        may_profile=True,
        may_assumptions=True,
        _run_assumptions=SimpleNamespace(new_assumptions=["a1", "a2"]),
        _stagnation_shadow=None,
        _disagreement_shadow=[],
        _cp=SimpleNamespace(save_respond=lambda **kw: None),
    )


# ---------------------------------------------------------------------------
# User profile
# ---------------------------------------------------------------------------

def test_a_healthy_profile_update_logs_and_replaces_the_profile():
    """The baseline, so the failure case is measured against something real."""
    agent = _Agent()
    _finalize(agent)

    assert "user_profile_update" in agent.log.events
    assert agent.last_user_profile is not None
    assert agent.sensor_failures == []


def test_a_broken_profile_update_is_reported_rather_than_looking_skipped():
    agent = _Agent(profile_boom=True)
    _finalize(agent)

    assert agent.sensor_failures == ["user_profile_update"]
    # The healthy event is genuinely absent — which is why the sensor is what
    # separates "failed" from "was not due".
    assert "user_profile_update" not in agent.log.events
    assert agent.last_user_profile is None


def test_a_profile_that_was_not_due_is_not_reported_as_a_failure():
    """The other side: silence must stay available where it is honest.

    Turning every absent update into a sensor failure would trade one
    indistinguishable pair for another.
    """
    agent = _Agent()
    agent._finalize_run_tail(
        "готовый ответ",
        user_question="вопрос",
        artifacts={},
        planner_out=SimpleNamespace(reasoning="r", sources=[]),
        replan_exhausted=False,
        may_profile=False,
        may_assumptions=True,
        _run_assumptions=SimpleNamespace(new_assumptions=[]),
        _stagnation_shadow=None,
        _disagreement_shadow=[],
        _cp=SimpleNamespace(save_respond=lambda **kw: None),
    )

    assert "user_profile_update" not in agent.log.events
    assert agent.sensor_failures == []


# ---------------------------------------------------------------------------
# Assumption store — the worse hiding place
# ---------------------------------------------------------------------------

def test_a_healthy_run_writes_its_assumptions_to_the_store():
    agent = _Agent()
    _finalize(agent)

    assert agent.assumption_store.saved == ["a1", "a2"]
    assert agent.sensor_failures == []


def test_a_lost_assumption_write_is_reported_rather_than_leaving_no_trace():
    """The point of this half, stated as the thing that used to be impossible.

    Before the fix a run that lost every assumption produced the same journal
    as one that saved them all, and `last_assumptions` still reported them —
    it is set from the in-memory object, not from the store.
    """
    agent = _Agent(store_boom=True)
    _finalize(agent)

    assert agent.assumption_store.saved == []
    assert agent.sensor_failures == ["assumption_store_save"]


def test_the_agent_still_reports_assumptions_it_failed_to_persist():
    """Pinned because it is the reason the silence was so effective.

    `last_assumptions` comes from the run object, so it looks correct even
    though nothing reached the store. Not changed here — the in-memory value is
    genuinely what this turn assumed — but it means the SENSOR is the only
    signal there is, and a future edit that drops it puts the hiding place back.
    """
    agent = _Agent(store_boom=True)
    _finalize(agent)

    assert agent.last_assumptions is not None
    assert agent.last_assumptions.new_assumptions == ["a1", "a2"]
    assert agent.assumption_store.saved == []


# ---------------------------------------------------------------------------
# Neither failure may take the turn down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("kwargs", [
    {"profile_boom": True},
    {"store_boom": True},
    {"profile_boom": True, "store_boom": True},
])
def test_neither_failure_costs_the_user_their_answer(kwargs):
    """An answer that is ready must not be lost to a bookkeeping failure."""
    agent = _Agent(**kwargs)
    answer, _verification, _weak = _finalize(agent)

    assert answer == "готовый ответ"
    assert "respond" in agent.log.events
