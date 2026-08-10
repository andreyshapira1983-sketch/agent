"""Два рубежа обучения не имеют права судить об одном эпизоде по-разному.

ЖИВОЙ СЛУЧАЙ 2026-08-10, замер по журналу и по диску. Прогон, чей ответ был
признан самоопровергнувшимся, получил `usage_eligible=False` — новый рубеж
сработал. И тот же прогон получил `procedure_feedback applied=1,
verdict=success`: счётчик успехов ЕДИНСТВЕННОЙ активной процедуры
`proc_0f1dea51` вырос, `updated_at=03:20:28`, то есть ровно этим прогоном.

Карантин кандидатов при этом работает: `smart_memory` исключает `candidate` из
выдачи планировщику, и живой трейс это подтвердил (`excluded_candidate: 24`).
Но активная процедура из выдачи НЕ исключается, а в активные попадают по
счётчику успехов. Утечка шла не через новую запись, а через кредит старой.

Причина — расхождение предикатов: `decide_usage_eligibility` читает
`defect_signals`, `procedure_credit_allowed` — нет. Здесь пинится их согласие.
"""
from __future__ import annotations

import pytest

from core.smart_memory import (
    EpisodeRecord,
    decide_usage_eligibility,
    feedback_for_episode,
    procedure_credit_allowed,
)

_ANSWER = "Facts:\n- trace_860e7ef записал 92 события.\nUnverified:\n- trace_860e7ef не доказан.\n"


def _episode(*, defect_signals: tuple[str, ...] = ()) -> EpisodeRecord:
    """Эпизод, по всем ПРЕЖНИМ осям безупречный: achieved, success, есть инструменты."""
    return EpisodeRecord(
        goal="докажи что ты существуешь",
        question="докажи что ты существуешь",
        outcome="success",
        summary="разбор",
        full_answer=_ANSWER,
        completion_state="achieved",
        verified_chunks=32,
        unverified_chunks=0,
        answer_quality_score=1.0,
        tools_used=["file_read", "shell_exec"],
        source_labels=["shell:whoami"],
        used_procedure_ids=["proc_0f1dea51"],
        defect_signals=list(defect_signals),
    )


def test_a_clean_episode_still_credits() -> None:
    """ПРЕДУСЛОВИЕ: кредит не заклинен в положении «отказать»."""
    clean = _episode()
    assert procedure_credit_allowed(clean)
    assert feedback_for_episode(clean) == "success"
    assert decide_usage_eligibility(clean)


def test_a_self_contradicted_episode_credits_nothing() -> None:
    """ГЛАВНОЕ: тот самый прогон, что поднял счётчик активной процедуры."""
    bad = _episode(defect_signals=("reasoning_action_mismatch", "self_contradiction"))
    assert not procedure_credit_allowed(bad), (
        "самоопровергнувшийся прогон всё ещё повышает стояние процедуры"
    )
    assert feedback_for_episode(bad) != "success"


def test_the_two_gates_cannot_disagree() -> None:
    """Инвариант, а не совпадение: непригодный к использованию не кредитует.

    Проверяется на обеих сторонах, чтобы правка одного предиката без другого
    снова развела их — именно это и произошло 2026-08-10.
    """
    for signals in [(), ("self_contradiction",),
                    ("reasoning_action_mismatch", "self_contradiction")]:
        ep = _episode(defect_signals=signals)
        eligible = decide_usage_eligibility(ep)
        credits = procedure_credit_allowed(ep)
        assert credits <= eligible, (
            f"эпизод кредитует, но использоваться не может: signals={signals}"
        )


@pytest.mark.parametrize("signal", ["self_contradiction"])
def test_the_disqualifying_signal_is_one_shared_name(signal: str) -> None:
    """Один список на оба рубежа: разойтись они могут только вместе."""
    from core.smart_memory import DISQUALIFYING_DEFECT_SIGNALS

    assert signal in DISQUALIFYING_DEFECT_SIGNALS


def test_an_unrelated_signal_does_not_block_credit() -> None:
    """Ломка наоборот: не всякий сигнал дефекта отменяет заслугу процедуры.

    `reasoning_action_mismatch` сам по себе говорит о расхождении плана и
    рассуждения, а не о ложности ответа. Отменять по нему кредит значило бы
    наказывать процедуру за чужую ошибку — ровно то, что MIR-057 уже запретил.
    """
    ep = _episode(defect_signals=("reasoning_action_mismatch",))
    assert procedure_credit_allowed(ep)
