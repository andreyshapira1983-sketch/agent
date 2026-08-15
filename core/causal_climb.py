"""Подъём по причинной лестнице: гипотезы, вмешательства, обобщение.

`core/causal_lesson.py` описывает состояния и условия перехода; здесь — сами
переходы. Разделение намеренное: там правила судейства, тут ходы игрока, и
судья не должен зависеть от того, кто ходит.

## Что здесь механизм, а что — суждение

Механизм: сверка условий, исполнение вмешательства, сопоставление предсказания
с измерением, запрет самоподтверждения.

Суждение: САМИ гипотезы. Их выдвигает автор — оператор, агент или внешний
инженер, — и `propose_explanation` только оформляет предложенное. Модуль не
сочиняет объяснений и не выбирает между ними: выбор без соперника — это и есть
то, по чему бьёт ступень `EXPLAINED`.

## Правило, ради которого модуль существует

`Intervention.observed` заполняется ТОЛЬКО из того, что вернул runner. Строку
нельзя передать напрямую, и это не удобство API: если «наблюдалось» можно
написать, то `ATTRIBUTED` означает «я уверен», а лестница заведена ровно чтобы
такое отвергать. Runner, ничего не измеривший, — ошибка, а не пустой результат.

Несбывшееся предсказание опровергает утверждение, а не откладывает его. Это
информация, и прятать её значило бы учить систему, что неудачная гипотеза
дешевле честной.

Подъём кончается на `GENERALIZED`. Последний шаг — назвать область, в которой
правило доказано, — оставлен человеку: у машины есть только те случаи, которые
она прогнала, а область это утверждение о границе, где правило ПРОВЕРЕНО.

Зачем и где граница: docs/CODE_NOTES.md, «The climb, and where it stops being
wiring».
"""
from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace

from core.causal_lesson import (
    CausalAuthor,
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
)


@dataclass(frozen=True)
class MeasuredOutcome:
    """Что вмешательство ДЕЙСТВИТЕЛЬНО показало.

    `matched_prediction` отделено от `summary` нарочно: сводка — текст для
    человека, а совпало ли предсказание, решает тот, кто запускал, и это
    решение обязано быть отдельным полем, а не вычитываться из слов.
    """

    command: str
    summary: str
    matched_prediction: bool

    def as_observed(self) -> str:
        return f"{self.command}: {self.summary}"


Runner = Callable[[], MeasuredOutcome | None]


def propose_explanation(
    statement: str, *, author: CausalAuthor = "agent", predicts: str = "",
) -> Explanation:
    """Оформить предложенную гипотезу. Сочиняет её автор, не этот модуль."""
    return Explanation(statement=statement, author=author, predicts=predicts)


def attach_explanations(
    claim: CausalClaim,
    explanations: Sequence[Explanation],
    *,
    chosen: str = "",
    violated_invariant: str = "",
) -> CausalClaim:
    """Привязать конкурирующие объяснения и выбор к наблюдению.

    Ничего не проверяет сверх записи: судит `state_of`, и он же скажет, чего
    не хватает — одного объяснения мало, двух живых тоже.
    """
    return replace(
        claim,
        explanations=tuple(explanations),
        chosen=chosen,
        violated_invariant=violated_invariant,
    )


def run_intervention(
    claim: CausalClaim,
    *,
    mutated: str,
    predicted: str,
    runner: Runner,
) -> CausalClaim:
    """Поставить вмешательство и записать ИЗМЕРЕННОЕ.

    `runner` обязан вернуть `MeasuredOutcome`. Вернул `None` — значит ничего не
    измерил, и это ошибка вызывающего, а не пустой результат: тихо записать
    «не наблюдалось» означало бы выдать уверенность за доказательство.
    """
    outcome = runner()
    if outcome is None:
        raise ValueError(
            "вмешательство без измерения: runner ничего не вернул, а `observed` "
            "заполняется только измеренным"
        )
    if not outcome.matched_prediction:
        return replace(
            claim,
            intervention=Intervention(
                mutated=mutated, predicted=predicted, observed=outcome.as_observed(),
            ),
            refuted_reason=(
                f"предсказание не сбылось: ожидалось «{predicted}», "
                f"измерено «{outcome.as_observed()}»"
            ),
        )
    return replace(
        claim,
        intervention=Intervention(
            mutated=mutated, predicted=predicted, observed=outcome.as_observed(),
        ),
    )


def try_generalization(
    claim: CausalClaim,
    *,
    rule: str,
    case_ref: str,
    runner: Runner,
) -> CausalClaim:
    """Проверить правило на ДРУГОМ случае.

    Совпадение с порождающим случаем — ошибка вызывающего, а не отрицательный
    результат: самоподтверждение запрещено всем, включая оператора, и молча
    вернуть «не выдержало» значило бы спутать запрет с исходом.
    """
    origin = claim.observation.episode_id
    if case_ref.strip() == origin.strip():
        raise ValueError(
            "обобщение проверяется на исходном случае; независимого свидетельства нет"
        )
    outcome = runner()
    if outcome is None:
        raise ValueError(
            "проверка обобщения без измерения: runner ничего не вернул"
        )
    return replace(
        claim,
        generalized_rule=rule,
        generalization=GeneralizationTest(
            case_ref=case_ref, origin_ref=origin, held=outcome.matched_prediction,
        ),
    )


def refute(explanation: Explanation, reason: str) -> Explanation:
    """Снять соперника с доски, назвав ЧЕМ он снят.

    Пустая причина не принимается: «объяснение отброшено» без основания — это
    выбор без соперника, переодетый в разбор.
    """
    if not reason.strip():
        raise ValueError("соперник снимается основанием, а не молчанием")
    return replace(explanation, refuted_by=reason)


def name_scope(claim: CausalClaim, scope: str) -> CausalClaim:
    """Назвать область, в которой правило доказано, — последний шаг.

    Оставлен человеку: у машины есть только прогнанные ею случаи, а область —
    утверждение о границе, где правило ПРОВЕРЕНО. См. `applies_to`.
    """
    return replace(claim, scope=scope)
