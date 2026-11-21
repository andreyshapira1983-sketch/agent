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

from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
    claim_tags,
    is_lesson,
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


def test_a_fully_proven_claim_becomes_a_lesson() -> None:
    """Полный путь пройден — право появляется."""
    claim = _claim()
    assert state_of(claim) == "LESSON"
    assert is_lesson(claim)
    assert "lesson" in claim_tags(claim)


def test_intermediate_states_stay_out_of_the_planner() -> None:
    """`lesson` — единственный обход карантина; гипотезам он не выдаётся."""
    for claim in (CausalClaim(observation=_obs()), _claim(intervention=None)):
        tags = claim_tags(claim)
        assert "lesson" not in tags
        assert any(t.startswith("causal:") for t in tags)


def test_retrieval_eligibility_is_computed_not_stored() -> None:
    assert _claim().to_log_payload()["retrieval_eligible"] is True
    assert CausalClaim(observation=_obs()).to_log_payload()["retrieval_eligible"] is False
