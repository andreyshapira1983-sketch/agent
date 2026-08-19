"""Two banked specimens: recurrence without investigation; gap without a name.

Operator exam 2026-08-17 («построй некую модель — как будешь действовать?»):
needs_clarification=False, the answer went to governance recital, relevance
0.20; reasoning_action_mismatch stood at occurrences=20 while the episode
banked success/usage_eligible=False. Two DIFFERENT gaps, banked separately
by operator ruling so that fixing one symptom cannot quietly close the
whole class.

Falsification note (recorded on purpose): our first hypothesis was
«повторение никто не потребляет». FALSIFIED by reading the code — the
reflection engine consumes repetition (min_occurrences=2) and emits a
LearningPlan whose files are actually ingested. The narrowed claim, which
these tests pin: the consumer converts a defect signal into READING, not
into causal investigation, and no consumer exists in the conversational
loop at all.

SECOND correction, 2026-08-19 evening — about the EXAMPLE, not the
invariant. The recurring signal used to illustrate this bank,
`reasoning_action_mismatch`, is itself substantially unreliable, and that
was measured two weeks ago in a WIP branch that never merged
(wip/mir-015-structural-justification, 2026-08-05: 44 firings over 108
real planner turns, accusations that «do not survive reading»). Verified
here today against main: the detector's keyword table knows 13 tools while
the registry holds 15 — `file_write`, `python_probe` and
`lesson_provenance` are invisible to it, so planning them is flagged by
construction — and two entries it DOES hold (`self_repair`,
`spawn_subagent`) name no registered tool at all, i.e. it accuses the
planner of omitting steps it cannot produce. The invariant these tests
protect is unchanged: repeated defect signals must open an investigation.
What changes is what such an investigation should find FIRST — suspect the
sensor before the reasoning.
"""
from __future__ import annotations

from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]


def _machine_sources() -> dict[str, str]:
    """Production code the MACHINE drives — cli/ is the operator's hand and
    deliberately excluded: an operator typing :causal is not the agent
    noticing its own recurring defect."""
    out: dict[str, str] = {}
    for pattern in ("core/*.py", "app/*.py"):
        for p in _REPO.glob(pattern):
            out[p.name] = p.read_text(encoding="utf-8", errors="replace")
    for name in ("agent_tick.py", "main.py"):
        p = _REPO / name
        if p.is_file():
            out[name] = p.read_text(encoding="utf-8", errors="replace")
    return out


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-17 and banked rather than fixed: a "
        "defect signal at occurrences=20 produced a LearningPlan (read the "
        "weak-point files) and nothing else — no competing hypotheses, no "
        "discriminating measurement, no root cause / UNKNOWN. The invariant "
        "this bank protects: recurrence past a threshold must open a causal "
        "INVESTIGATION — in this system's own vocabulary, explanations "
        "attached to a claim on the ladder and a measurement that can kill "
        "them — initiated by the machine, not by the operator typing "
        ":causal. Reading lists do not satisfy it. The implementation is "
        "deliberately unprescribed."
    ),
    strict=True,
)
def test_recurrence_opens_a_machine_investigation() -> None:
    sources = _machine_sources()
    sources.pop("causal_climb.py", None)  # the ladder itself, not a caller
    callers = [
        name for name, text in sources.items()
        if "attach_explanations(" in text or "propose_explanation(" in text
    ]
    assert callers, (
        "no machine path turns a recurring defect signal into competing "
        "explanations on the causal ladder; today the only hand that climbs "
        "is the operator's (cli/commands_causal.py)"
    )


@pytest.mark.xfail(
    reason=(
        "KNOWN GAP, measured 2026-08-17 and banked rather than fixed: the "
        "loop has a rich ambiguity apparatus and NO concept for a knowledge "
        "gap. Live baseline: the exam question raised no clarification "
        "(needs_clarification=False — the requirement WAS understood), yet "
        "the answer masked missing competence with governance recital "
        "(relevance 0.20). The semantic this bank protects: when the "
        "requirement is understood and the agent's own measured data gives "
        "no ground to claim it knows how to build the thing, it must be "
        "able to NAME «мне не хватает знания» — distinct from clarification "
        "and from governance — and move toward acquiring/verifying the "
        "missing knowledge. Not prescribed: web_search reflexes, "
        "LearningPlan, any KnowledgeGapDetector class, or a competence "
        "formula (three similar successes must NOT mint an expert). The "
        "token 'knowledge_gap' here is a name-marker, not a design: whoever "
        "closes this renames freely — the distinction matters, not the word."
    ),
    strict=True,
)
def test_the_loop_can_name_a_knowledge_gap() -> None:
    sources = _machine_sources()
    assert any("knowledge_gap" in text for text in sources.values()), (
        "no production structure distinguishes 'requirement understood but "
        "competence insufficient' from ambiguity; the only 'I don't know' "
        "the loop can express is needs_clarification"
    )


def test_the_ambiguity_half_of_the_distinction_exists() -> None:
    """The boundary pin: ambiguity IS structurally represented (that half of
    the distinction works — the exam proved it by correctly staying quiet),
    so a future fix must ADD the second concept, not rename the first."""
    sources = _machine_sources()
    assert any("needs_clarification" in text for text in sources.values())
