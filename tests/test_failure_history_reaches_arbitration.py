"""`failure_history` is a live signal into the completion arbiter — pinned.

The channel carries typed failures from the attempt loop (and from
verification, which appends to the same list) to three consumers. Emptying it
at any one of the three left all 7159 tests green, which said nothing about
the channel and everything about what the suite watches: two of the three
consumers put the value into an LLM prompt, and the mock ignores prompts.

Arbitration is the third consumer and the only one with no model in the path:

    failure_history -> failure_codes -> _discloses(answer, *codes)
                    -> failed_but_reported | silently_missing
                    -> defect signal -> completion_state

So the bite is available here without mocking anything. What the test pins is
the DIFFERENCE the channel makes: with codes present, an answer that owns up
to the failure is judged differently from one that stays quiet. Empty the
channel and the two collapse into the same verdict — the run can no longer
tell an honest report from silence.

Level of proof, measured rather than claimed. Deleting the distinction inside
`evaluate_completion_obligations` reddens this file; emptying the argument at
the call site in `loop.py` does NOT, because these tests call the function
directly. So this pins the RECIPIENT'S DECISION, not the wiring that feeds it,
and it says nothing about what the user finally sees.
"""
from __future__ import annotations

from core.completion_obligation import evaluate_completion_obligations

QUESTION = "прочитай core/loop.py и скажи, что там"
CODE = "approval_deny"


def _judge(*, answer: str, codes: tuple[str, ...]):
    """One obligation, unmet: the question names a path, no artifact came back."""
    result = evaluate_completion_obligations(
        question=QUESTION,
        answer=answer,
        artifacts={},
        failure_codes=codes,
    )
    assert result.required, "the named path must create an obligation"
    return [o.status for o in result.obligations]


def test_a_disclosed_failure_is_judged_differently_from_a_silent_one() -> None:
    disclosed = _judge(answer=f"Не смог прочитать loop.py: {CODE}.", codes=(CODE,))
    silent = _judge(answer="Вот краткое содержание файла.", codes=(CODE,))

    assert disclosed == ["failed_but_reported"], disclosed
    assert silent == ["silently_missing"], silent


def test_emptying_the_channel_collapses_that_difference() -> None:
    """The mutation this file exists for: no codes, no distinction.

    With `failure_codes=()` the honest answer and the quiet one receive the
    same verdict. That is what emptying `failure_history` does to arbitration,
    and until now nothing in the suite noticed it.
    """
    disclosed = _judge(answer=f"Не смог прочитать loop.py: {CODE}.", codes=())
    silent = _judge(answer="Вот краткое содержание файла.", codes=())

    assert disclosed == silent == ["silently_missing"], (disclosed, silent)


def test_the_channel_only_ever_softens_a_verdict_it_does_not_invent_success() -> None:
    """GUARD: a failure code must not turn an unmet duty into a satisfied one."""
    for codes in ((), (CODE,), ("timeout", CODE)):
        for answer in (f"Не смог: {CODE}.", "Вот содержание."):
            statuses = _judge(answer=answer, codes=codes)
            assert "satisfied" not in statuses, (codes, answer, statuses)
