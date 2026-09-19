import pytest

from core.synth_resilience import (
    SynthAttempt,
    build_degraded_synthesis_answer,
    classify_model_error,
    is_transient_error,
    run_synthesizer_ladder,
)


class _Err(Exception):
    def __init__(self, msg, status_code=None):
        super().__init__(msg)
        self.status_code = status_code


class _Budget(Exception):
    pass


def _events_collector():
    events = []

    def on_event(name, payload):
        events.append((name, payload))

    return events, on_event


def test_classify_bad_request():
    assert classify_model_error(_Err("Could not finish the message", 400)) == "bad_request"
    assert classify_model_error(_Err("Invalid request", 422)) == "bad_request"
    assert classify_model_error(_Err("could not finish")) == "bad_request"


def test_classify_transient():
    assert classify_model_error(_Err("rate limited", 429)) == "rate_limit"
    assert classify_model_error(_Err("boom", 503)) == "server_error"
    assert classify_model_error(_Err("request timed out")) == "timeout"
    assert is_transient_error(_Err("boom", 500)) is True
    assert is_transient_error(_Err("bad", 400)) is False


def test_success_first_attempt_no_retry():
    calls = []

    def do(attempt: SynthAttempt) -> str:
        calls.append(attempt)
        return "ANSWER"

    events, on_event = _events_collector()
    res = run_synthesizer_ladder(do, build_degraded_answer=build_degraded_synthesis_answer, on_event=on_event)
    assert res.answer == "ANSWER"
    assert res.attempts == 1
    assert res.degraded is False
    assert len(calls) == 1
    assert calls[0].adapt_context is False
    assert events == []


def test_recovers_on_plain_retry():
    calls = []

    def do(attempt: SynthAttempt) -> str:
        calls.append(attempt)
        if attempt.index == 0:
            raise _Err("Could not finish the message", 400)
        return "RECOVERED"

    events, on_event = _events_collector()
    res = run_synthesizer_ladder(do, build_degraded_answer=build_degraded_synthesis_answer, on_event=on_event)
    assert res.answer == "RECOVERED"
    assert res.attempts == 2
    assert res.degraded is False
    # second attempt (index 1) is not final here -> plain retry, no adapt
    assert calls[1].adapt_context is False
    names = [n for n, _ in events]
    assert "synthesizer_attempt_failed" in names
    assert "synthesizer_recovered" in names


def test_recovers_after_adapt_on_final_attempt():
    calls = []

    def do(attempt: SynthAttempt) -> str:
        calls.append(attempt)
        if not attempt.adapt_context:
            raise _Err("Could not finish the message", 400)
        return "ADAPTED"

    res = run_synthesizer_ladder(do, build_degraded_answer=build_degraded_synthesis_answer)
    assert res.answer == "ADAPTED"
    assert res.attempts == 3
    assert res.degraded is False
    assert calls[0].adapt_context is False
    assert calls[1].adapt_context is False
    assert calls[2].adapt_context is True
    assert calls[2].is_final is True


def test_degrades_after_three_failures_no_fourth_attempt():
    calls = []

    def do(attempt: SynthAttempt) -> str:
        calls.append(attempt)
        raise _Err("Could not finish the message", 400)

    events, on_event = _events_collector()
    res = run_synthesizer_ladder(do, build_degraded_answer=build_degraded_synthesis_answer, on_event=on_event)
    assert res.degraded is True
    assert res.attempts == 3
    assert len(calls) == 3  # never a fourth identical attempt
    assert res.final_error_class == "bad_request"
    assert "## Conclusion" in res.answer
    assert "## Unverified" in res.answer
    assert [n for n, _ in events].count("synthesizer_attempt_failed") == 3
    assert "synthesizer_degraded" in [n for n, _ in events]


def test_fatal_type_propagates_immediately():
    calls = []

    def do(attempt: SynthAttempt) -> str:
        calls.append(attempt)
        raise _Budget("out of budget")

    with pytest.raises(_Budget):
        run_synthesizer_ladder(
            do,
            build_degraded_answer=build_degraded_synthesis_answer,
            fatal_types=(_Budget,),
        )
    assert len(calls) == 1  # no retry on fatal


def test_degraded_answer_has_contract_headers_and_low_confidence():
    ans = build_degraded_synthesis_answer(["BadRequestError: Could not finish the message"], "bad_request")
    for header in ("## Conclusion", "## Facts", "## Unverified", "## Confidence"):
        assert header in ans
    assert "low" in ans.lower()


class TestBlankDraftIsNotAnAnswer:
    """Measured on the live agent 2026-08-02: the synthesizer billed 14336
    output tokens, reported status=success, and returned the empty string.
    The ladder accepted it on the first attempt, the run banked
    outcome=success, and the operator received a 110-character evidence
    notice with nothing under it. A blank draft must walk the same
    retry -> adapt -> honest-partial path an exception does."""

    def _run(self, produce):
        calls: list[int] = []

        def _synth(attempt):
            calls.append(attempt.index)
            return produce(attempt)

        result = run_synthesizer_ladder(
            _synth, build_degraded_answer=build_degraded_synthesis_answer
        )
        return calls, result

    def test_empty_draft_is_retried_then_degraded(self):
        calls, result = self._run(lambda _a: "")
        assert len(calls) == 3, "a blank draft was accepted without retrying"
        assert result.degraded is True
        assert result.answer.strip(), "the honest partial must carry text"
        assert result.final_error_class == "blank_answer"

    def test_whitespace_only_draft_counts_as_blank(self):
        calls, result = self._run(lambda _a: "   \n\t  \n")
        assert len(calls) == 3
        assert result.degraded is True

    def test_recovery_on_the_second_attempt_is_reported(self):
        def _flaky(attempt):
            return "" if attempt.index == 0 else "the real answer"

        calls, result = self._run(_flaky)
        assert len(calls) == 2
        assert result.degraded is False
        assert result.answer == "the real answer"
        assert result.attempts == 2

    def test_normal_answer_still_lands_on_the_first_attempt(self):
        calls, result = self._run(lambda _a: "an ordinary answer")
        assert len(calls) == 1
        assert result.degraded is False
        assert result.answer == "an ordinary answer"
