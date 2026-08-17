"""Хранилище причинных утверждений выше первой ступени + выжимка уроков.

`core/causal_store.py` хранит нижнюю ступень (OBSERVED, с дедупликацией
повторов); подниматься было не с чего — состояния выше жили только в памяти
процесса (MIR-096). Здесь утверждение переживает ход целиком: наблюдение,
конкурирующие объяснения с их опровержениями, вмешательство, обобщение,
область — и состояние НЕ хранится, а вычисляется `state_of` при каждом чтении
(инвариант 5 лестницы: хранимый флаг рано или поздно разошёлся бы с условием).

Предохранитель оператора 2026-08-15: наблюдения не конденсируются в «истину».
«3 раза модель выдумала параметр» само по себе даёт только повод расследовать;
в планировщик проходит ЕДИНСТВЕННО состояние LESSON, то есть полная лестница —
гипотеза, проверка вмешательством, обобщение на ином случае, названная область.
`distilled_lessons` — единственная дверь, и она отдаёт выжимку («как изменить
следующее действие»), а не помойку из воспоминаний.

Зачем: docs/CODE_NOTES.md, "Observations are not lessons".
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.causal_lesson import (
    CausalClaim,
    Explanation,
    GeneralizationTest,
    Intervention,
    Observation,
    is_lesson,
    proven_cases,
    state_of,
)
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl

_CLAIMS_FILENAME = "causal_claims.jsonl"


@dataclass(frozen=True)
class LessonCard:
    """Выжимка урока для планировщика: как изменить следующее действие.

    `machine_action` — имя механического применения (например,
    ``include_real_signatures``): урок меняет ПЛАН, а не украшает подсказку
    прозой в надежде, что модель её прочтёт.
    """

    rule: str
    scope: str
    directive: str
    machine_action: str = ""
    evidence: tuple[str, ...] = ()
    cases: tuple[str, ...] = ()
    #: Store key (cclaim_…) — the identity a delivery receipt names.
    key: str = ""


def _claims_path(workspace: str | Path) -> Path:
    return Path(workspace) / "data" / _CLAIMS_FILENAME


def claim_key(claim: CausalClaim) -> str:
    """Одно утверждение — одна строка: подъём меняет состояние, не личность."""
    raw = claim.observation.episode_id + "|" + "|".join(
        sorted(claim.observation.defect_signals)
    )
    return "cclaim_" + hashlib.sha256(raw.encode("utf-8")).hexdigest()[:12]


def claim_to_dict(claim: CausalClaim) -> dict[str, Any]:
    obs = claim.observation
    return {
        "observation": {
            "episode_id": obs.episode_id, "trace_id": obs.trace_id,
            "run_id": obs.run_id, "defect_signals": list(obs.defect_signals),
            "evidence_refs": list(obs.evidence_refs),
            "observed_mismatch": obs.observed_mismatch,
        },
        "explanations": [
            {"statement": e.statement, "author": e.author,
             "predicts": e.predicts, "refuted_by": e.refuted_by}
            for e in claim.explanations
        ],
        "chosen": claim.chosen,
        "violated_invariant": claim.violated_invariant,
        "intervention": (
            {"mutated": claim.intervention.mutated,
             "predicted": claim.intervention.predicted,
             "observed": claim.intervention.observed,
             "restored": claim.intervention.restored}
            if claim.intervention else None
        ),
        "generalized_rule": claim.generalized_rule,
        "scope": claim.scope,
        "falsifiers": list(claim.falsifiers),
        "generalization": (
            {"case_ref": claim.generalization.case_ref,
             "origin_ref": claim.generalization.origin_ref,
             "held": claim.generalization.held}
            if claim.generalization else None
        ),
        "refuted_reason": claim.refuted_reason,
        "notes": list(claim.notes),
    }


def claim_from_dict(row: dict[str, Any]) -> CausalClaim:
    obs = row.get("observation") or {}
    inter = row.get("intervention")
    gen = row.get("generalization")
    return CausalClaim(
        observation=Observation(
            episode_id=str(obs.get("episode_id") or ""),
            trace_id=str(obs.get("trace_id") or ""),
            run_id=str(obs.get("run_id") or ""),
            defect_signals=tuple(obs.get("defect_signals") or ()),
            evidence_refs=tuple(obs.get("evidence_refs") or ()),
            observed_mismatch=str(obs.get("observed_mismatch") or ""),
        ),
        explanations=tuple(
            Explanation(
                statement=str(e.get("statement") or ""),
                author=e.get("author") or "agent",
                predicts=str(e.get("predicts") or ""),
                refuted_by=str(e.get("refuted_by") or ""),
            )
            for e in row.get("explanations") or ()
        ),
        chosen=str(row.get("chosen") or ""),
        violated_invariant=str(row.get("violated_invariant") or ""),
        intervention=(
            Intervention(
                mutated=str(inter.get("mutated") or ""),
                predicted=str(inter.get("predicted") or ""),
                observed=str(inter.get("observed") or ""),
                restored=bool(inter.get("restored")),
            ) if inter else None
        ),
        generalized_rule=str(row.get("generalized_rule") or ""),
        scope=str(row.get("scope") or ""),
        falsifiers=tuple(row.get("falsifiers") or ()),
        generalization=(
            GeneralizationTest(
                case_ref=str(gen.get("case_ref") or ""),
                origin_ref=str(gen.get("origin_ref") or ""),
                held=bool(gen.get("held")),
            ) if gen else None
        ),
        refuted_reason=str(row.get("refuted_reason") or ""),
        notes=tuple(row.get("notes") or ()),
    )


def save_claim(
    claim: CausalClaim,
    *,
    workspace: str | Path,
    directive: str = "",
    machine_action: str = "",
) -> str:
    """Записать/обновить утверждение. Возвращает его ключ.

    `directive` и `machine_action` — операционная половина будущего урока
    («как изменить следующее действие»); хранятся рядом с утверждением, но
    права на планировщик не дают: его даёт только `state_of` == LESSON.
    """
    path = _claims_path(workspace)
    key = claim_key(claim)
    rows = [r for r in read_state_jsonl(path) if r.get("key") != key]
    rows.append({
        "key": key,
        "claim": claim_to_dict(claim),
        "directive": directive,
        "machine_action": machine_action,
        "state": state_of(claim),  # денормализовано ДЛЯ ЧТЕНИЯ ЧЕЛОВЕКОМ; судит state_of
    })
    rewrite_state_jsonl(path, rows)
    return key


def load_claims(
    workspace: str | Path,
) -> tuple[tuple[CausalClaim, dict[str, Any]], ...]:
    """Все утверждения с их операционными полями (directive, machine_action)."""
    out: list[tuple[CausalClaim, dict[str, Any]]] = []
    for row in read_state_jsonl(_claims_path(workspace)):
        claim_row = row.get("claim")
        if not isinstance(claim_row, dict):
            continue
        extra = {
            "directive": str(row.get("directive") or ""),
            "machine_action": str(row.get("machine_action") or ""),
            "key": str(row.get("key") or ""),
        }
        out.append((claim_from_dict(claim_row), extra))
    return tuple(out)


def distilled_lessons(workspace: str | Path) -> tuple[LessonCard, ...]:
    """ЕДИНСТВЕННАЯ дверь в планировщик: только состояние LESSON, выжимкой.

    Всё промежуточное — гипотезы, недоказанные вмешательства, непроверенные
    обобщения — остаётся видимым в хранилище и невидимым для планов.
    """
    cards: list[LessonCard] = []
    for claim, extra in load_claims(workspace):
        if not is_lesson(claim):
            continue
        cards.append(LessonCard(
            rule=claim.generalized_rule,
            scope=claim.scope,
            directive=extra["directive"] or claim.generalized_rule,
            machine_action=extra["machine_action"],
            evidence=claim.observation.evidence_refs,
            cases=proven_cases(claim),
            key=extra["key"],
        ))
    return tuple(cards)
