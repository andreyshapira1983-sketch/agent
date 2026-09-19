"""Продвигает доказательство, а не подпись.

РЕШЕНИЕ ОПЕРАТОРА 2026-08-11. Первая версия ставила условием личность автора —
агенту запрещалось подтверждать себя. Отвергнуто: тогда право даёт подпись, и
достаточно объявить кого-нибудь «доверенным», чтобы обойти защиту целиком.

Здесь автор не входит ни в одно условие. Продвигают три вещи, и каждая
закрывает свой способ ошибиться:

    EXPLAINED    — единственное объяснение, принятое потому, что другого не
                   искали;
    ATTRIBUTED   — корреляция, названная причиной;
    GENERALIZED  — правило, верное лишь там, где найдено.

Последнее — честная замена запрету самоподтверждения: проверять обобщение на
породившем случае нельзя НИКОМУ, включая оператора.
"""
from __future__ import annotations

import pytest

from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
    blocking_reason,
    claim_tags,
    is_lesson,
    observation_from_episode,
    state_of,
)

_ORIGIN = "run_a1aee861"


def _obs(**kw) -> Observation:
    base = {
        "episode_id": "ep-x", "trace_id": "trace_x", "run_id": _ORIGIN,
        "defect_signals": ("user_contract_unrepresented",),
        "evidence_refs": ("file:core/completion_contract.py", "log:trace_x"),
        "observed_mismatch": "U05=POSSIBLE, но U08 не исполнен",
    }
    base.update(kw)
    return Observation(**base)


def _rivals() -> tuple[Explanation, ...]:
    return (
        Explanation(
            statement="план не перенёс зависимость U05->U08",
            author="agent", predicts="в плане нет шага под U08",
        ),
        Explanation(
            statement="единицы не дошли до контракта",
            author="operator", predicts="requested_units пуст",
            refuted_by="requested_units содержал 19 записей",
        ),
    )


def _claim(**kw) -> CausalClaim:
    base = {
        "observation": _obs(),
        "explanations": _rivals(),
        "chosen": "план не перенёс зависимость U05->U08",
        "violated_invariant": "исходный контракт авторитетен после декомпозиции",
        "intervention": Intervention(
            mutated="в план добавлен шаг под U08",
            predicted="завершение перестанет считать задачу выполненной",
            observed="U08 исполнен, unaddressed_units пуст",
            restored=True,
        ),
        "generalized_rule": (
            "производная единица плана обязана нести связь со своей единицей "
            "контракта, а завершение считается от единиц контракта"
        ),
        "scope": "любая многочастная задача с объявленными единицами",
        "falsifiers": ("задача без объявленных единиц",),
        "generalization": GeneralizationTest(
            case_ref="run_f785f6e4", origin_ref=_ORIGIN, held=True
        ),
    }
    base.update(kw)
    return CausalClaim(**base)


def test_the_machine_stops_at_observation() -> None:
    """ГЛАВНОЕ: автомат производит только замеченное отклонение."""

    class _Ep:
        id = "ep-1"
        run_id = "run_1"
        defect_signals: tuple[str, ...] = ("self_contradiction",)
        source_labels: tuple[str, ...] = ("file:core/loop.py",)
        completion_state = "partially_achieved"

    obs = observation_from_episode(_Ep(), trace_id="trace_1")
    assert obs is not None
    assert "причина не доказана" in obs.observed_mismatch
    bare = CausalClaim(observation=obs)
    assert state_of(bare) == "OBSERVED"
    assert not is_lesson(bare)


def test_a_single_explanation_does_not_advance() -> None:
    """Объяснение, принятое потому, что другого не искали."""
    claim = _claim(explanations=(_rivals()[0],), chosen="")
    assert state_of(claim) == "OBSERVED"
    assert "конкурирующие" in blocking_reason(claim)


def test_unresolved_rivals_do_not_advance() -> None:
    """Пока живы двое, причина не установлена."""
    alive_both = tuple(
        Explanation(statement=e.statement, author=e.author, predicts=e.predicts)
        for e in _rivals()
    )
    claim = _claim(explanations=alive_both)
    assert "соперники не разобраны" in blocking_reason(claim)
    assert not is_lesson(claim)


def test_correlation_without_intervention_is_not_a_cause() -> None:
    """ATTRIBUTED требует вмешательства, а не совпадения."""
    claim = _claim(intervention=None)
    assert state_of(claim) == "EXPLAINED"
    assert "вмешательством" in blocking_reason(claim)


def test_a_rule_untested_on_a_new_case_is_not_a_lesson() -> None:
    claim = _claim(generalization=None)
    assert state_of(claim) == "ATTRIBUTED"
    assert "на новом случае" in blocking_reason(claim)


def test_the_generalization_case_must_differ_from_the_origin() -> None:
    """ЗАМЕНА ЗАПРЕТУ САМОПОДТВЕРЖДЕНИЯ, и она ни от кого не зависит."""
    claim = _claim(
        generalization=GeneralizationTest(
            case_ref=_ORIGIN, origin_ref=_ORIGIN, held=True
        )
    )
    assert "по исходному случаю" in blocking_reason(claim)
    assert not is_lesson(claim)


def test_a_rule_that_failed_the_new_case_is_not_a_lesson() -> None:
    claim = _claim(
        generalization=GeneralizationTest(
            case_ref="run_other", origin_ref=_ORIGIN, held=False
        )
    )
    assert "не выдержало" in blocking_reason(claim)


def test_a_fully_proven_claim_becomes_a_lesson() -> None:
    """Полный путь пройден — право появляется."""
    claim = _claim()
    assert state_of(claim) == "LESSON"
    assert is_lesson(claim)
    assert "lesson" in claim_tags(claim)


@pytest.mark.parametrize("author", ["agent", "operator", "external_engineer"])
def test_authorship_changes_nothing(author: str) -> None:
    """ГЛАВНЫЙ ИНВАРИАНТ: подпись не входит в условие продвижения.

    Один и тот же набор свидетельств даёт один и тот же результат, кто бы ни
    выдвинул объяснение — включая агента о самом себе.
    """
    rivals = (
        Explanation(statement="план не перенёс зависимость", author=author,
                    predicts="в плане нет шага"),
        Explanation(statement="единицы не дошли", author=author,
                    predicts="units пуст", refuted_by="units было 19"),
    )
    claim = _claim(explanations=rivals, chosen="план не перенёс зависимость")
    assert is_lesson(claim)


def test_a_refutation_is_terminal_and_visible() -> None:
    """Опровергнутое не исчезает и обратно не поднимается переписыванием."""
    claim = _claim(refuted_reason="вмешательство не воспроизвелось")
    assert state_of(claim) == "REFUTED"
    assert not is_lesson(claim)
    assert "lesson" not in claim_tags(claim)


def test_intermediate_states_stay_out_of_the_planner() -> None:
    """`lesson` — единственный обход карантина; гипотезам он не выдаётся."""
    for claim in (CausalClaim(observation=_obs()), _claim(intervention=None)):
        tags = claim_tags(claim)
        assert "lesson" not in tags
        assert any(t.startswith("causal:") for t in tags)


def test_retrieval_eligibility_is_computed_not_stored() -> None:
    assert _claim().to_log_payload()["retrieval_eligible"] is True
    assert CausalClaim(observation=_obs()).to_log_payload()["retrieval_eligible"] is False


def test_observation_without_evidence_blocks_first() -> None:
    """Без улик наблюдения не начинают даже объяснять."""
    claim = _claim(observation=_obs(evidence_refs=()))
    assert "нет улик" in blocking_reason(claim)
