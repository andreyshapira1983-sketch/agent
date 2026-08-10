"""От замеченного отклонения до усвоенного правила — состояниями, не подписями.

РЕШЕНИЕ ОПЕРАТОРА 2026-08-11, второе и главное. Первая версия этого модуля
ставила условием продвижения ЛИЧНОСТЬ автора: агенту запрещалось подтверждать
себя. Это отвергнуто, и справедливо — тогда право на урок давала подпись, а не
доказательство, и достаточно было завести «доверенного» автора, чтобы обойти
всю защиту.

Здесь автор не участвует в продвижении вовсе. Оператор, агент и внешний
инженер — лишь возможные АВТОРЫ гипотез. Продвигает же три вещи: улики,
причинное доказательство и проверка обобщения.

## Состояния и почему их именно столько

    OBSERVED     отклонение замечено; собирается машиной
    EXPLAINED    названы КОНКУРИРУЮЩИЕ объяснения — минимум два
    ATTRIBUTED   одно объяснение доказано вмешательством, соперники разобраны
    GENERALIZED  правило проверено на СЛУЧАЕ, отличном от исходного
    LESSON       право влиять на будущие планы
    REFUTED      опровергнуто; остаётся видимым и обратно не поднимается

Каждое состояние снимает ровно одну возможность ошибиться, и ни одно не
снимает чужую. `EXPLAINED` бьёт по единственному объяснению, принятому потому,
что другое не искали. `ATTRIBUTED` бьёт по корреляции, названной причиной.
`GENERALIZED` бьёт по правилу, верному лишь там, где его нашли, — и это же
единственная честная замена запрету самоподтверждения: проверять обобщение на
породившем случае нельзя никому, включая оператора.

## Инварианты

  1. Состояния не перескакиваются: каждое требует предыдущего.
  2. Автор не входит ни в одно условие продвижения.
  3. Свидетельство обобщения обязано отличаться от исходного случая.
  4. Причина доказывается ВМЕШАТЕЛЬСТВОМ, а не совпадением: названо, что
     менялось и что при этом наблюдалось.
  5. `retrieval_eligible` вычисляется, а не хранится: хранимый флаг рано или
     поздно разойдётся с условием, его породившим.
  6. Опровержение терминально — обратно поднимают новой гипотезой, а не
     переписыванием старой.

## Минимальная безопасная первая реализация

Машина производит только `OBSERVED`. Всё выше требует принесённого
свидетельства: соперничающих объяснений, записи вмешательства, случая для
проверки обобщения. Ничего из этого нельзя вывести из сигнала детектора, и
попытка была отвергнута накануне.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

CausalState = Literal[
    "OBSERVED", "EXPLAINED", "ATTRIBUTED", "GENERALIZED", "LESSON", "REFUTED"
]

#: Возможные авторы гипотез. Список существует для ПРОВЕНАНСА и измерения
#: точности авторов, и намеренно не участвует в продвижении.
CausalAuthor = Literal["operator", "agent", "external_engineer"]

_ORDER: tuple[str, ...] = ("OBSERVED", "EXPLAINED", "ATTRIBUTED", "GENERALIZED", "LESSON")


@dataclass(frozen=True)
class Observation:
    """Замеченное отклонение. Единственное, что машина производит сама."""

    episode_id: str
    trace_id: str
    run_id: str
    defect_signals: tuple[str, ...] = ()
    evidence_refs: tuple[str, ...] = ()
    observed_mismatch: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "episode_id": self.episode_id, "trace_id": self.trace_id,
            "run_id": self.run_id, "defect_signals": list(self.defect_signals),
            "evidence_refs": list(self.evidence_refs),
            "observed_mismatch": self.observed_mismatch,
        }


@dataclass(frozen=True)
class Explanation:
    """Одно из конкурирующих объяснений отклонения."""

    statement: str
    author: CausalAuthor
    #: Что наблюдалось бы, будь оно верным, — и чего не наблюдалось бы иначе.
    predicts: str = ""
    refuted_by: str = ""

    @property
    def alive(self) -> bool:
        return not self.refuted_by.strip()


@dataclass(frozen=True)
class Intervention:
    """Доказательство причины вмешательством, а не совпадением.

    Названо трижды: что меняли, что предсказывали, что наблюдали. Совпадение
    без вмешательства причиной не становится — это и отличает `ATTRIBUTED` от
    красивого рассказа.
    """

    mutated: str
    predicted: str
    observed: str
    restored: bool = False

    @property
    def proves_cause(self) -> bool:
        return bool(
            self.mutated.strip() and self.predicted.strip() and self.observed.strip()
        )


@dataclass(frozen=True)
class GeneralizationTest:
    """Проверка правила на случае, отличном от исходного."""

    case_ref: str
    origin_ref: str
    held: bool = False

    @property
    def independent(self) -> bool:
        """Свидетельство обязано быть НЕ породившим случаем."""
        a, b = self.case_ref.strip(), self.origin_ref.strip()
        return bool(a) and bool(b) and a != b


@dataclass(frozen=True)
class CausalClaim:
    """Гипотеза о причине со всем, что для неё уже принесено."""

    observation: Observation
    explanations: tuple[Explanation, ...] = ()
    chosen: str = ""
    violated_invariant: str = ""
    intervention: Intervention | None = None
    generalized_rule: str = ""
    scope: str = ""
    falsifiers: tuple[str, ...] = ()
    generalization: GeneralizationTest | None = None
    refuted_reason: str = ""
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            **self.observation.to_log_payload(),
            "state": state_of(self),
            "explanations": [
                {"statement": e.statement, "author": e.author, "alive": e.alive}
                for e in self.explanations
            ],
            "chosen": self.chosen,
            "violated_invariant": self.violated_invariant,
            "intervention_proves_cause": bool(
                self.intervention and self.intervention.proves_cause
            ),
            "generalized_rule": self.generalized_rule,
            "generalization_independent": bool(
                self.generalization and self.generalization.independent
            ),
            "generalization_held": bool(
                self.generalization and self.generalization.held
            ),
            "retrieval_eligible": state_of(self) == "LESSON",
        }


def blocking_reason(claim: CausalClaim) -> str:
    """Чего не хватает для СЛЕДУЮЩЕГО состояния — одной причиной, поимённо.

    Пустая строка означает «дальше некуда»: либо это уже урок, либо
    опровержение. Пропуск обязан быть читаемым, иначе он неотличим от полноты.
    """
    if claim.refuted_reason.strip():
        return ""
    if not claim.observation.evidence_refs:
        return "нет улик наблюдения"
    alive = [e for e in claim.explanations if e.alive]
    if len(claim.explanations) < 2:
        return "нужны конкурирующие объяснения, минимум два"
    if not claim.chosen.strip():
        return "не выбрано объяснение"
    if len(alive) > 1:
        return "соперники не разобраны: живых объяснений больше одного"
    if not claim.violated_invariant.strip():
        return "не назван нарушенный инвариант"
    if claim.intervention is None or not claim.intervention.proves_cause:
        return "причина не доказана вмешательством"
    if not claim.generalized_rule.strip():
        return "не сформулировано обобщаемое правило"
    if claim.generalization is None:
        return "правило не проверено на новом случае"
    if not claim.generalization.independent:
        return "проверка обобщения идёт по исходному случаю"
    if not claim.generalization.held:
        return "правило не выдержало проверку на новом случае"
    return ""


def state_of(claim: CausalClaim) -> CausalState:
    """Состояние — функция от принесённого, а не от того, кто принёс."""
    if claim.refuted_reason.strip():
        return "REFUTED"
    reason = blocking_reason(claim)
    if not reason:
        return "LESSON"
    if reason.startswith(("не сформулировано", "правило не проверено",
                          "проверка обобщения", "правило не выдержало")):
        return "ATTRIBUTED"
    if reason.startswith(("не назван", "причина не доказана")):
        return "EXPLAINED"
    return "OBSERVED"


def is_lesson(claim: CausalClaim) -> bool:
    """Заслужило ли утверждение право влиять на будущие планы."""
    return state_of(claim) == "LESSON"


def claim_tags(claim: CausalClaim) -> tuple[str, ...]:
    """`lesson` появляется ТОЛЬКО в конечном состоянии.

    Промежуточные состояния остаются видимыми человеку и своими тегами, но путь
    в планировщик им закрыт: `lesson` — единственный обход карантина.
    """
    state = state_of(claim)
    if state == "LESSON":
        return ("lesson", "causal", "generalization-tested")
    return (f"causal:{state.lower()}", "unverified")


def observation_from_episode(episode: Any, *, trace_id: str) -> Observation | None:
    """Отклонение из эпизода: три выводимые вещи и ни одной невыводимой.

    `None`, когда детекторы молчали: отклонений без сигнала этот сборщик не
    видит и притворяться, что видит, не будет.
    """
    signals = tuple(getattr(episode, "defect_signals", None) or ())
    if not signals:
        return None
    completion = getattr(episode, "completion_state", None) or "unknown"
    return Observation(
        episode_id=str(getattr(episode, "id", "") or ""),
        trace_id=trace_id,
        run_id=str(getattr(episode, "run_id", "") or ""),
        defect_signals=signals,
        evidence_refs=tuple(getattr(episode, "source_labels", None) or ()),
        observed_mismatch=(
            f"детекторы {', '.join(signals)} при завершении {completion}; "
            "объяснения не выдвинуты, причина не доказана"
        ),
    )
