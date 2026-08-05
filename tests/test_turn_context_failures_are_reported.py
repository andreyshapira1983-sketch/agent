"""A failure while reading the turn context must leave a trace.

Census item A3. Two handlers in `core/loop_context.py` swallowed without a
word, and the layer already had the cure — `_sensor_failed` — which this file
could not call because it was never declared in its host contract. The means
existed; the connection did not.

Measured before the fix, and the two failures hid differently:

    referent resolution   healthy: logs `referent_decision`, sets a decision
                          broken : NO event, decision None — the
                                   local-critique path silently never engages

    assumption extraction healthy: 2 assumptions registered
                          broken : 0 registered, and NO event either way

The second is the worse hiding place. `assumptions_registered` fires later, in
`core/loop_attempt.py`, and only when the registry is non-empty — so a crash
here produces exactly the silence of a question with nothing to assume. Two
different causes, one indistinguishable picture, which is the shape MIR-077 was
closed for and the shape the census found still living in 28 places.

What is NOT changed by the fix: the referent handler still sets
`last_referent_decision = None`. That is the fail-safe half — a resolver that
threw must not enable a path that depends on it. Reporting and failing safe are
different jobs, and the old code did only the second.
"""
from __future__ import annotations

from typing import Any
from unittest.mock import patch

import pytest

from core.loop_context import AgentLoopContext


class _Log:
    trace_id = "trace"
    log_dir = None

    def __init__(self) -> None:
        self.events: list[str] = []

    def log(self, name, payload=None, **kw):  # noqa: ANN001 - test spy
        self.events.append(name)


class _Agent(AgentLoopContext):
    """Only what the two paths touch — no runtime needed to ask this."""

    def __init__(self) -> None:
        self.log = _Log()
        self.memory = None
        self.user_profile_store = None
        self.last_user_profile = None
        self.last_self_analysis = None
        self.last_referent_decision: Any = "UNSET"
        self.sensor_failures: list[str] = []

    def _file_read_workspace_root(self):
        return None

    def _sensor_failed(self, sensor: str, exc: BaseException) -> None:
        self.sensor_failures.append(sensor)
        self.log.log("sensor_failed")


# ---------------------------------------------------------------------------
# The mixin must be able to call the cure at all
# ---------------------------------------------------------------------------

def test_the_file_declares_the_sensor_it_needs():
    """The root cause, pinned separately from the symptom.

    Both handlers were silent because `_sensor_failed` was not in the host
    contract, so the file could not reach it through the MRO in a way the
    layer's own convention would admit. Declaring it is the fix; this checks
    the declaration rather than trusting that the calls below happen to work.
    """
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(__import__(
        "core.loop_context", fromlist=["x"]
    )))
    declared: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test):
            declared |= {
                sub.target.id
                for sub in ast.walk(node)
                if isinstance(sub, ast.AnnAssign) and isinstance(sub.target, ast.Name)
            }
    assert "_sensor_failed" in declared


# ---------------------------------------------------------------------------
# Referent resolution
# ---------------------------------------------------------------------------

def test_a_healthy_referent_resolution_logs_its_decision():
    """The baseline, so the failure case is measured against something real."""
    agent = _Agent()
    with patch("core.loop_context.referent_resolver_mode", return_value="shadow"):
        agent._maybe_resolve_referent("вопрос", file_hint=None)

    assert "referent_decision" in agent.log.events
    assert agent.last_referent_decision != "UNSET"
    assert agent.sensor_failures == []


def test_a_broken_referent_resolution_is_reported_and_fails_safe():
    agent = _Agent()
    with patch("core.loop_context.referent_resolver_mode", return_value="shadow"), \
         patch("core.loop_context.ReferentResolver", side_effect=RuntimeError("boom")):
        agent._maybe_resolve_referent("вопрос", file_hint=None)

    # Reported: the run no longer looks like one where nothing resolved.
    assert agent.sensor_failures == ["referent_resolution"]
    # Fail-safe half kept: a resolver that threw may not enable the path.
    assert agent.last_referent_decision is None
    # And the healthy event is genuinely absent, so the two are distinguishable
    # by the journal rather than only by the sensor list.
    assert "referent_decision" not in agent.log.events


# ---------------------------------------------------------------------------
# Assumption extraction — the better hiding place
# ---------------------------------------------------------------------------

def test_a_healthy_extraction_registers_assumptions():
    agent = _Agent()
    registry, _cp = agent._open_run("сколько строк в файле core/loop.py?")

    assert len(registry) > 0
    assert agent.sensor_failures == []


def test_a_broken_extraction_is_reported_rather_than_looking_empty():
    """The point of A3, stated as the thing that used to be impossible.

    An empty registry means one of two things — the question carried no
    assumptions, or extraction crashed. Before the fix both produced the same
    empty registry and the same absent event. Now only one of them names a
    sensor.
    """
    agent = _Agent()
    with patch("core.loop_context.extract_from_question",
               side_effect=RuntimeError("boom")):
        registry, _cp = agent._open_run("сколько строк в файле core/loop.py?")

    assert len(registry) == 0
    assert agent.sensor_failures == ["assumption_extraction"]


def test_an_empty_question_is_not_reported_as_a_failure():
    """The other side of the same coin: silence must stay available.

    A question with nothing to assume is not a defect, and turning every empty
    registry into a sensor failure would trade one indistinguishable pair for
    another.
    """
    agent = _Agent()
    with patch("core.loop_context.extract_from_question", return_value=[]):
        registry, _cp = agent._open_run("привет")

    assert len(registry) == 0
    assert agent.sensor_failures == []


# ---------------------------------------------------------------------------
# Neither failure may take the turn down
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("target", [
    "core.loop_context.ReferentResolver",
    "core.loop_context.extract_from_question",
])
def test_neither_failure_aborts_the_run(target):
    agent = _Agent()
    with patch(target, side_effect=RuntimeError("boom")), \
         patch("core.loop_context.referent_resolver_mode", return_value="shadow"):
        agent._maybe_resolve_referent("вопрос", file_hint=None)
        registry, cp = agent._open_run("вопрос")

    assert registry is not None
    assert cp is not None
