"""Подъём по причинной лестнице: гипотезы, вмешательства, обобщение."""
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
    """Что вмешательство ДЕЙСТВИТЕЛЬНО показало."""

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
    """Поставить вмешательство и записать ИЗМЕРЕННОЕ."""
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
    """Проверить правило на ДРУГОМ случае."""
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
