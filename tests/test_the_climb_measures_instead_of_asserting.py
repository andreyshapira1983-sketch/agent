"""Climbing means measuring. A sentence is not an intervention.

Background: docs/CODE_NOTES.md, "The climb, and where it stops being wiring".
"""
from __future__ import annotations

import pytest

from core.causal_climb import (
    MeasuredOutcome,
    attach_explanations,
    name_scope,
    propose_explanation,
    refute,
    run_intervention,
    try_generalization,
)
from core.causal_lesson import CausalClaim, Observation, state_of

_OBS = Observation(
    episode_id="ep_1", trace_id="tr_1", run_id="run_1",
    defect_signals=("citation_fabricated",),
    evidence_refs=("file:core/verifier_core.py",),
    observed_mismatch="детекторы citation_fabricated при завершении achieved",
)


def _claim() -> CausalClaim:
    return CausalClaim(observation=_OBS)


def _rival_refuted():
    return (
        propose_explanation(
            "the citation grammar was never shown to the model",
            author="agent", predicts="the answer would carry no labels at all",
        ),
        propose_explanation(
            "resolution short-circuits evaluation",
            author="agent", predicts="a false claim with a resolving label scores verified",
        ),
    )


def test_one_explanation_is_not_competing_explanations():
    """The rung exists to strike at the single explanation nobody contested."""
    climbed = attach_explanations(
        _claim(), _rival_refuted()[:1], chosen="resolution short-circuits evaluation",
    )
    assert state_of(climbed) == "OBSERVED"


def test_rivals_must_be_settled_not_merely_listed():
    """Two live explanations is not a choice — it is an unfinished argument."""
    climbed = attach_explanations(
        _claim(), _rival_refuted(), chosen="resolution short-circuits evaluation",
    )
    assert state_of(climbed) == "OBSERVED"


def test_a_settled_argument_reaches_explained():
    both = _rival_refuted()
    settled = (refute(both[0], "the answer did carry labels — see the trace"), both[1])
    climbed = attach_explanations(
        _claim(), settled, chosen="resolution short-circuits evaluation",
    )
    assert state_of(climbed) == "EXPLAINED"


def _explained() -> CausalClaim:
    both = _rival_refuted()
    settled = (refute(both[0], "the answer did carry labels — see the trace"), both[1])
    return attach_explanations(
        _claim(), settled,
        chosen="resolution short-circuits evaluation",
        violated_invariant="a verdict follows the claim's truth, not its label",
    )


def test_an_intervention_without_a_measurement_is_refused():
    """The core of this module. `observed` must come from a runner that RAN,
    never from a sentence someone wrote — otherwise ATTRIBUTED means «I am
    confident», which is exactly the state this ladder exists to refuse.
    """
    with pytest.raises(ValueError, match="без измерения"):
        run_intervention(
            _explained(),
            mutated="reverted the resolution short-circuit",
            predicted="the false claim stops scoring verified",
            runner=lambda: None,  # measured nothing
        )


def test_a_measured_intervention_reaches_attributed():
    climbed = run_intervention(
        _explained(),
        mutated="reverted the resolution short-circuit",
        predicted="the false claim stops scoring verified",
        runner=lambda: MeasuredOutcome(
            command="pytest tests/test_verifier_derived_claims.py",
            summary="1 failed, 7 passed",
            matched_prediction=True,
        ),
    )
    assert state_of(climbed) == "ATTRIBUTED"
    assert climbed.intervention is not None
    assert "1 failed" in climbed.intervention.observed


def test_a_prediction_that_did_not_come_true_refutes_the_claim():
    """A failed prediction is information, not a setback to be hidden."""
    climbed = run_intervention(
        _explained(),
        mutated="reverted the resolution short-circuit",
        predicted="the false claim stops scoring verified",
        runner=lambda: MeasuredOutcome(
            command="pytest tests/test_verifier_derived_claims.py",
            summary="8 passed",
            matched_prediction=False,
        ),
    )
    assert state_of(climbed) == "REFUTED"


def _attributed() -> CausalClaim:
    return run_intervention(
        _explained(),
        mutated="reverted the resolution short-circuit",
        predicted="the false claim stops scoring verified",
        runner=lambda: MeasuredOutcome(
            command="pytest tests/test_verifier_derived_claims.py",
            summary="1 failed, 7 passed",
            matched_prediction=True,
        ),
    )


def test_generalization_on_the_origin_case_is_refused():
    """Self-confirmation is the one thing this rung exists to refuse, and the
    module says so: checking a rule on the case that produced it is forbidden
    to everyone, the operator included.
    """
    with pytest.raises(ValueError, match="исходн"):
        try_generalization(
            _attributed(),
            rule="a resolving label may not decide a verdict",
            case_ref="ep_1",
            runner=lambda: MeasuredOutcome("x", "ok", matched_prediction=True),
        )


def test_an_independent_case_that_held_reaches_generalized():
    climbed = try_generalization(
        _attributed(),
        rule="a resolving label may not decide a verdict",
        case_ref="ep_2",
        runner=lambda: MeasuredOutcome("x", "1 failed", matched_prediction=True),
    )
    assert state_of(climbed) == "GENERALIZED"


def test_scope_is_the_last_step_and_it_is_the_operator_s():
    """The climb stops here on purpose: naming the область is a judgement about
    where the rule was PROVEN, and the machine has only the cases it ran.
    """
    climbed = try_generalization(
        _attributed(),
        rule="a resolving label may not decide a verdict",
        case_ref="ep_2",
        runner=lambda: MeasuredOutcome("x", "1 failed", matched_prediction=True),
    )
    assert state_of(climbed) == "GENERALIZED"
    assert state_of(name_scope(climbed, "citation-resolving verdicts")) == "LESSON"


def test_a_wrapped_pair_is_still_a_pair() -> None:
    """Живой прогон 2026-09-20: подъём отказал шесть раз подряд — «выжило 0».
    Модель писала пары, но перенос строки внутри объяснения (и пустая строка
    перед предсказанием) ломали разбор, требовавший двух СОСЕДНИХ строк."""
    from core.causal_climb_action import _parse_hypotheses

    wrapped = ("ОБЪЯСНЕНИЕ 1: В ходе чтения книги модель сформировала вывод,\n"
               "который не попал в план шага\n\n"
               "ПРЕДСКАЗАНИЕ 1: в logs/daemon_tick.jsonl будет событие без шага\n\n"
               "ОБЪЯСНЕНИЕ 2: детектор сработал на пересказе цитаты\n"
               "ПРЕДСКАЗАНИЕ 2: в data/episodic_memory.jsonl tools_used пуст")
    pairs = _parse_hypotheses(wrapped)
    assert len(pairs) == 2, pairs
    assert pairs[0][0].startswith("В ходе чтения книги")
    assert "daemon_tick.jsonl" in pairs[0][1]
    assert pairs[1][0].startswith("детектор сработал")

    plain = ("ОБЪЯСНЕНИЕ: причина А\nПРЕДСКАЗАНИЕ: в журнале есть X\n"
             "ОБЪЯСНЕНИЕ: причина Б\nПРЕДСКАЗАНИЕ: в журнале нет X")
    assert len(_parse_hypotheses(plain)) == 2, "номер пары необязателен"
    assert _parse_hypotheses("никаких пар тут нет") == []


def test_nothing_left_to_explain_is_not_a_failure(tmp_path) -> None:
    """Живой прогон 2026-09-20: все наблюдения закрылись, и шесть циклов подряд
    записали «failed» там, где работа была сделана — драйв поломок считал их
    сломанными действиями."""
    from types import SimpleNamespace

    from core.causal_climb_action import explain_causal_observation

    (tmp_path / "data").mkdir()
    out = explain_causal_observation(agent=SimpleNamespace(log=None), workspace=tmp_path)
    assert out.result == "idle", out
    assert not out.work_done
