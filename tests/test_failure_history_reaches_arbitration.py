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

import json
from pathlib import Path

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


# ==========================================================================
# The call-site wire, which the tests above deliberately do not reach.
# The module docstring records the old limit: emptying the argument at the
# call site in the loop left everything green. This closes that limit.
# ==========================================================================
def _run_disclosing_cycle(workspace: Path):
    """One real cycle that fails (plan_parse_failed) and OWNS UP in the answer."""
    from core.model_usage import ModelUsageLimits
    from tests.conftest import FakeLLM
    from tests.test_budget_resume import _build_guarded_agent

    llm = FakeLLM(
        responses=[
            "this is not a plan at all",   # planner: parse failure -> trigger
            "Не смог прочитать doc.txt: plan_parse_failed. Продолжить нечем.",
        ]
    )
    agent = _build_guarded_agent(workspace, llm, ModelUsageLimits())
    agent.run(user_question="прочитай doc.txt и скажи, что там")
    events = [
        json.loads(line)
        for line in agent.log.path.read_text(encoding="utf-8").splitlines()
    ]
    return llm, events


def test_the_loop_call_site_feeds_real_codes_to_the_arbiter(workspace: Path) -> None:
    """failure_history -> codes -> obligation verdict, through the real run.

    The disclosing answer must be judged `failed_but_reported`. Cut the codes
    at the call site (loop_run_tail) and the same run collapses to
    `silently_missing` — which is exactly what the unit tests above could
    never see, because they call the arbiter directly.
    """
    _llm, events = _run_disclosing_cycle(workspace)

    obligation = [e for e in events if e.get("event") == "completion_obligation"]
    assert obligation, "the run must journal its obligation verdict"
    payload = json.dumps(obligation[-1], ensure_ascii=False)
    assert "failed_but_reported" in payload, (
        f"a disclosed failure must be judged as reported, got: {payload[:400]}"
    )
    assert "silently_missing" not in payload


def test_exhausted_replan_puts_the_failure_block_into_the_synthesis_prompt(
    workspace: Path,
) -> None:
    """Settles the UNDER_QUESTION pair from the map, pole one.

    With replanning exhausted, the synthesizer's prompt must carry the
    <failure_context> block — hypothesis (a) 'value goes into the prompt,
    the mock ignores it' is the true mechanism FOR EXHAUSTED runs.
    """
    llm, _events = _run_disclosing_cycle(workspace)

    synth_prompts = " || ".join(c["user"] for c in llm.calls)
    assert "<failure_context>" in synth_prompts, (
        "an exhausted run must show its failures to the synthesizer"
    )


def test_unexhausted_replan_sends_no_failure_block_to_synthesis(
    workspace: Path,
) -> None:
    """Pole two: history non-empty, replanning NOT exhausted -> gate sends None.

    Hypothesis (b) from the map: loop_synthesis:637 hands the synthesizer
    None unless st.replan_exhausted, so for a run that failed once and then
    recovered, the prompt must carry NO failure block at all.
    """
    from core.model_usage import ModelUsageLimits
    from tests.conftest import FakeLLM
    from tests.test_budget_resume import _build_guarded_agent

    llm = FakeLLM(
        responses=[
            "still not a plan",                       # attempt 1: parse failure
            '{"reasoning":"no tools","sources":[]}',  # attempt 2: recovers
            "Ответ по существу.",
        ]
    )
    agent = _build_guarded_agent(
        workspace, llm, ModelUsageLimits(), max_replan_attempts=2
    )
    agent.run(user_question="прочитай doc.txt и скажи, что там")

    synth_prompts = " || ".join(c["user"] for c in llm.calls[1:])
    assert "<failure_context>" not in synth_prompts, (
        "the gate must withhold failure history from synthesis while "
        "replanning is not exhausted — that is the switch, working as wired"
    )
