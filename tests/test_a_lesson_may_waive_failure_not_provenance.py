"""The `lesson` exemption must waive what it claims to waive, and no more.

MIR-115 measured the shape: `decide_usage_eligibility` returns `True` for any
episode tagged `lesson`, and the machinery that writes the content also mints
the tag (`core/self_build_memory.py:122` tags EVERY self-build episode,
`core/self_repair.py:504` likewise). MIR-121 added the threat model: memory
injection is a mature attack class whose payload survives restarts and defeats
LLM-judge sanitisation.

But the exemption is NOT a bug in itself, and removing it would be worse than
the defect. Its purpose is to let a FAILURE be remembered as a warning — 101 of
the 127 lessons in the live store are failures, and that is the feature working.

The actual defect is narrower, and the function's own docstring names it: it
promises a lesson is "admitted whatever its OUTCOME" and then waives five axes.
Three of those are legitimately tied to failing — outcome, completion, and
verified chunks (a run that failed confirmed nothing, by definition). Two are
not:

  * `memory:` source labels — an episode whose evidence came from memory is
    memory citing itself. That is this repository's own "an echo is not a
    second witness" rule, and it is precisely the amplification step a
    poisoning attack needs.
  * relevance below the measured floor — nothing about failing makes an
    off-topic record worth steering by.

Measured before changing anything: of 127 lessons in the live store, **zero**
are memory-sourced and **zero** fall below the relevance floor. So this closes a
door nothing currently walks through — prophylaxis against the attack path, not
repair of live damage. Which makes the control tests below the important half:
a fix that quietly broke failure-as-warning would be a real regression traded
for a hypothetical one.
"""
from __future__ import annotations

import pytest

from core.smart_memory import EpisodeRecord, decide_usage_eligibility
from core.verification_summary import _LOW_RELEVANCE


def _lesson(**over) -> EpisodeRecord:
    """A failing lesson — the ordinary, legitimate case."""
    base = {
        "goal": "probe", "question": "probe?", "outcome": "failed",
        "summary": "the run failed and this is the warning",
        "tags": ("lesson", "self-build"), "verified_chunks": 0,
        "source_labels": ("file:core/loop.py",), "relevance_score": 0.9,
    }
    base.update(over)
    return EpisodeRecord(**base)


# --- THE CONTROL HALF: the legitimate function must survive -----------------

def test_a_failed_lesson_is_still_admitted() -> None:
    """101 of 127 live lessons are failures. If this reddens, the fix broke the
    feature it was meant to preserve."""
    assert decide_usage_eligibility(_lesson()) is True


def test_a_lesson_with_no_verified_chunks_is_still_admitted() -> None:
    """A run that failed confirmed nothing — demanding evidence of success from
    a record of failure is incoherent."""
    assert decide_usage_eligibility(_lesson(verified_chunks=0)) is True


def test_a_lesson_that_never_completed_is_still_admitted() -> None:
    assert decide_usage_eligibility(_lesson(outcome="failed")) is True


def test_an_ordinary_successful_episode_is_unaffected() -> None:
    """The non-lesson path must not move at all."""
    ok = EpisodeRecord(
        goal="g", question="q?", outcome="success", summary="s",
        tags=("episode",), verified_chunks=3,
        source_labels=("file:core/loop.py",), relevance_score=0.9,
        completion_state="achieved",
    )
    assert decide_usage_eligibility(ok) is True


# --- THE FIX HALF: what the exemption must stop waiving ---------------------

def test_a_lesson_fed_by_memory_is_refused() -> None:
    """An echo is not a second witness. This is the amplification step a memory
    poisoning attack needs, and the one path where a lesson can bootstrap
    itself into evidence."""
    echo = _lesson(source_labels=("memory:ep_1234", "file:core/loop.py"))
    assert decide_usage_eligibility(echo) is False, (
        "a lesson citing memory as its source was admitted — memory is now "
        "allowed to confirm itself"
    )


def test_an_off_topic_lesson_is_refused() -> None:
    """Failing does not make an irrelevant record worth steering by."""
    off = _lesson(relevance_score=_LOW_RELEVANCE - 0.01)
    assert decide_usage_eligibility(off) is False


def test_relevance_exactly_at_the_floor_is_still_admitted() -> None:
    """The boundary belongs to the record, not against it — and pinning it here
    means a later change to the floor cannot silently move this gate."""
    assert decide_usage_eligibility(_lesson(relevance_score=_LOW_RELEVANCE)) is True


def test_an_unmeasured_relevance_does_not_convict() -> None:
    """`None` means never measured, which is not the same as measured-and-bad.
    Fail-closed belongs where absence is suspicious; here it would refuse every
    lesson written before the field existed."""
    assert decide_usage_eligibility(_lesson(relevance_score=None)) is True


# --- the axis that was already ahead of the exemption, pinned ---------------

def test_a_self_refuted_answer_is_refused_even_as_a_lesson() -> None:
    """This check already ran BEFORE the exemption (MIR-060) and must stay
    there: an answer that refuted itself is not experience on any grounds."""
    refuted = _lesson(tags=("lesson",), defect_signals=("citation_fabricated",))
    assert decide_usage_eligibility(refuted) is False


@pytest.mark.parametrize("labels", [
    ("memory:ep_1",),
    ("memory:ep_1", "memory:ep_2"),
    ("file:a.py", "memory:ep_3"),
])
def test_any_memory_label_anywhere_disqualifies_a_lesson(labels) -> None:
    """One memory source is enough: a record part-sourced from memory still
    carries memory's claim into the future as if it were independent."""
    assert decide_usage_eligibility(_lesson(source_labels=labels)) is False
