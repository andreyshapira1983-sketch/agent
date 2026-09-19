"""Хранилище причинных утверждений выше первой ступени + выжимка уроков."""
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
    """Выжимка урока для планировщика: как изменить следующее действие."""

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
    """Записать/обновить утверждение. Возвращает его ключ."""
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
            # M2: a climb step's name in this field is provenance, not an
            # action for the planner; only the consumed vocabulary passes.
            machine_action=(
                extra["machine_action"]
                if extra["machine_action"] in LESSON_MACHINE_ACTIONS else ""
            ),
            evidence=claim.observation.evidence_refs,
            cases=proven_cases(claim),
            key=extra["key"],
        ))
    return tuple(cards)


# --- Доставка уроков планировщику: авторство агента (груз 2а, 2026-08-29). ---
# Его выбор (б): опровержения через load_claims, карточку не нагружаем.
#: Machine actions a LESSON may carry — the names code actually CONSUMES
#: (block 4, M2, 2026-09-03). The same store field also records which climb
#: step last touched a claim (`run_claim_experiment`, …); those are
#: provenance, not planner actions, and `distilled_lessons` does not pass
#: them on. Consumers: `core.self_task_producer._lesson_prompt_parts`.
LESSON_MACHINE_ACTIONS: frozenset[str] = frozenset({"include_real_signatures"})


def _lesson_tokens(text: str) -> frozenset[str]:
    return frozenset(
        w for w in "".join(c.lower() if c.isalnum() else " " for c in text).split()
        if len(w) > 2
    )


def lesson_applies(lesson: LessonCard, *, question: str = "", file_hint: str = "") -> bool:
    """Does this lesson belong in THIS plan? Unscoped call (no question, no
    file) keeps every lesson — the pre-block-4 behaviour. With a question,
    the lesson must share a word of its scope or a proven case with the
    question or name the hinted file. Measured (M3): 347 of 359 injections
    went into every planner turn regardless of scope."""
    if not question and not file_hint:
        return True
    hint = str(file_hint or "").replace("\\", "/").strip().casefold()
    if hint:
        names = (lesson.scope, *lesson.cases, *lesson.evidence)
        base = hint.rsplit("/", 1)[-1]
        if any(base and base in str(n).casefold() for n in names):
            return True
    asked = _lesson_tokens(question)
    if not asked:
        return False
    scoped = _lesson_tokens(lesson.scope) | frozenset(
        t for case in lesson.cases for t in _lesson_tokens(case)
    )
    return bool(asked & scoped)


def lesson_block_for_prompt(workspace, *, question: str = "", file_hint: str = "") -> str:
    """Build a '## Lessons for planning' block from the causal claim store.

    Returns an empty string when the store is empty or nothing applies.
    Each lesson is rendered as plain lines (not a table). Counter-evidence
    lines are included only when the claim has non-empty refuted_by texts.
    """
    lessons = tuple(
        card for card in distilled_lessons(workspace)
        if lesson_applies(card, question=question, file_hint=file_hint)
    )
    if not lessons:
        return ""

    claims = load_claims(workspace)
    claim_by_key = {extra["key"]: claim for claim, extra in claims}

    lines = ["## Lessons for planning"]
    for lesson in lessons:
        lines.append(f"- Rule: {lesson.rule}")
        lines.append(f"  Scope: {lesson.scope}")
        lines.append(f"  Directive: {lesson.directive}")
        if lesson.machine_action:
            # M2: the mechanical half of a lesson travels with the prose.
            lines.append(f"  Machine action: {lesson.machine_action}")
        cases = ", ".join(lesson.cases) if lesson.cases else ""
        lines.append(f"  Cases: {cases}")

        # Find the claim linked by key and collect its refuted_by texts.
        claim = claim_by_key.get(lesson.key)
        refuted_by = []
        if claim is not None:
            for expl in claim.explanations:
                if expl.refuted_by:
                    refuted_by.append(expl.refuted_by)
        if refuted_by:
            lines.append(f"  Counter-evidence: {'; '.join(refuted_by)}")

    return "\n".join(lines)
