"""The differentiating experiment: lesson OFF vs ON, everything else equal."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArmResult:
    generations: int
    measured: int  # generations the instruments actually examined
    defects: int


@dataclass(frozen=True)
class AbReport:
    lesson_key: str
    impl_path: str
    k: int
    off: ArmResult
    on: ArmResult
    verdict: str
    measurement_id: str

    def render(self) -> str:
        return (
            f"A/B по уроку {self.lesson_key} на {self.impl_path} (k={self.k})\n"
            f"  OFF (без урока): дефектов {self.off.defects} из "
            f"{self.off.measured} замеренных ({self.off.generations} генераций)\n"
            f"  ON  (с уроком):  дефектов {self.on.defects} из "
            f"{self.on.measured} замеренных ({self.on.generations} генераций)\n"
            f"  вердикт: {self.verdict}"
        )


def _defect_reason(test_content: str) -> str | None:
    """One phantom reason (kwargs or attribute), or None; unparsed = None
    AND excluded from `measured` by the caller."""
    from core.attribute_sieve import phantom_attribute_reason
    from core.self_task_producer import _phantom_kwargs_reason

    tree = ast.parse(test_content)  # SyntaxError propagates to the caller
    return _phantom_kwargs_reason(tree) or phantom_attribute_reason(test_content)


def _run_arm(llm: Any, k: int, lessons: tuple[Any, ...], **candidate: Any) -> ArmResult:
    from core.self_task_producer import _task_builder_generate

    measured = defects = 0
    for _ in range(k):
        build = _task_builder_generate(llm, lessons=lessons, **candidate)
        content = str(build.data.get("test_content") or "")
        if build.decision != "built" or not content.strip():
            continue
        try:
            reason = _defect_reason(content)
        except SyntaxError:
            continue  # the instrument never ran — not a measurement
        measured += 1
        if reason:
            defects += 1
    return ArmResult(generations=k, measured=measured, defects=defects)


def _verdict(off: ArmResult, on: ArmResult) -> str:
    if off.measured < 2 or on.measured < 2:
        return "insufficient_measurements"
    if off.defects > 0 and on.defects == 0:
        return "effect_observed"
    if off.defects == 0 and on.defects == 0:
        return "no_discrimination"
    if off.defects > 0 and on.defects > 0:
        return "lesson_insufficient"
    return "inverted"


def run_lesson_ab_experiment(
    workspace: str | Path,
    llm: Any,
    *,
    lesson_key: str,
    impl_path: str,
    quote: str,
    evidence_ref: str,
    current_content: str,
    k: int = 4,
) -> AbReport:
    """Run both arms and sign the verdict as a measurement of the lesson."""
    from core.causal_claim_store import distilled_lessons
    from core.lesson_provenance import record_lesson_measurement

    cards = tuple(
        c for c in distilled_lessons(workspace) if c.key == lesson_key
    )
    candidate = {
        "impl_path": impl_path, "quote": quote,
        "evidence_ref": evidence_ref, "current_content": current_content,
    }
    off = _run_arm(llm, k, (), **candidate)
    on = _run_arm(llm, k, cards, **candidate)
    verdict = _verdict(off, on)
    measurement_id = ""
    if verdict != "insufficient_measurements":
        measurement_id = record_lesson_measurement(
            workspace, lesson_key,
            instrument="ab_experiment",
            outcome=verdict,
            detail=(
                f"k={k} on {impl_path}; OFF {off.defects}/{off.measured} "
                f"phantoms, ON {on.defects}/{on.measured} phantoms"
            ),
        )
    return AbReport(
        lesson_key=lesson_key, impl_path=impl_path, k=k,
        off=off, on=on, verdict=verdict, measurement_id=measurement_id,
    )
