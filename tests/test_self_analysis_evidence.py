"""Issue #119 — self-analysis is evidence-classed, not force-verified.

The recorded live failure: the operator said the previous answer was wrong and
asked why. The planner correctly decided no tool was needed, the synthesizer
produced a substantive self-analysis, the verifier scored
``verified=0, unverified=10``, ``low_evidence_truncation`` suppressed 1287
characters, and the operator received "ни одно утверждение не подтверждено
источниками этого цикла".

These tests pin the whole contract:

* the exact prompt from the issue is recognised as a conversational correction;
* the session transcript supports claims **about the exchange** and nothing else;
* dialogue support is never laundered into ``verified``;
* an unsupported claim about the outside world is still filtered;
* the resulting episode is not banked as a clean success (so it cannot be
  promoted into procedural memory).

Fail-before evidence for the behavioural ones: on the pre-fix code every chunk
of the reproduced answer carried verdict ``unverified``, so
``evaluate_low_evidence_policy`` triggered and rewrote the answer.
"""
from __future__ import annotations

from pathlib import Path

from core.evidence import ProvenanceChain, evidence_from_prior_turn
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.low_evidence_policy import evaluate_low_evidence_policy
from core.memory import WorkingMemory
from core.policy import PolicyGate
from core.verifier import verify
from tests.conftest import FakeLLM, FakePlanner
from tools.base import ToolRegistry

#: The interactive prompt recorded in issue #119, verbatim.
ISSUE_119_PROMPT = (
    "плохо справился Ты не правильно ответила некорректно тебе надо тебя "
    "починить перепрограммировать заново и придумать что с этим делать "
    "Почему ответ не корректен"
)

PRIOR_QUESTION = "какие у тебя есть возможности"
PRIOR_ANSWER = (
    "Conclusion: я могу отвечать на общие вопросы. "
    "Facts:\n  - общие рекомендации по продукту [general-knowledge]"
)


def _dialogue_chain(turns: int = 1) -> ProvenanceChain:
    chain = ProvenanceChain()
    for i in range(1, turns + 1):
        chain.add(
            evidence_from_prior_turn(
                turn_id=f"turn_{i}",
                turn_index=i,
                question=PRIOR_QUESTION,
                answer=PRIOR_ANSWER,
            )
        )
    return chain


# ── 3. Verifier verdicts ─────────────────────────────────────────────────────

_SELF_ANALYSIS_ANSWER = "Conclusion:\nМой предыдущий ответ не отвечал на заданный вопрос.\nFacts:\n- Вы спросили про возможности, а я ответил общими рекомендациями.\n- Я не привёл ни одного конкретного примера в том ответе.\n- Мой ответ был построен как список советов, а не как ответ.\n- Я не уточнил, о каком именно контексте идёт речь в вопросе.\n- Вопрос требовал разбора, а я выдал шаблонную реплику.\n- Мой ответ не опирался ни на один источник этого цикла.\nSources: dialogue\nConfidence: low\nUnverified: nothing\nSafety: ok"


def test_dialogue_citation_on_a_world_claim_earns_no_support():
    """The transcript proves what was said — never an arbitrary outside fact."""
    answer = "Conclusion:\nСтавка ЦБ составляет 21% годовых [dialogue:turn_1].\nFacts:\n- Инфляция в прошлом месяце была 0.8% [dialogue:turn_1]."
    report = verify(answer=answer, chain=_dialogue_chain())
    assert report.verified_chunks == 0
    assert report.dialogue_supported_chunks == 0
    assert all(c.verdict != "dialogue_supported" for c in report.chunks)


# ── 4. The truncation gate ───────────────────────────────────────────────────


def test_external_claims_are_still_filtered_and_self_analysis_survives():
    """Mixed answer: the world claims go, the two dialogue claims stay."""
    mixed = "\n".join(
        [
            "Conclusion:",
            "Мой предыдущий ответ не отвечал на ваш вопрос.",
            "Facts:",
            "- Я привёл общие советы вместо ответа по существу.",
        ]
        + [f"- Ставка по вкладам сейчас {i} процентов." for i in range(1, 9)]
    )
    report = verify(answer=mixed, chain=_dialogue_chain())
    assert report.dialogue_supported_chunks == 2, report.to_log_payload()
    assert report.unverified_chunks == 8, report.to_log_payload()

    result = evaluate_low_evidence_policy(
        answer=mixed, report=report, question=ISSUE_119_PROMPT
    )
    assert result.triggered, result.to_log_payload()
    # The self-correction survives the truncation verbatim…
    assert "Мой предыдущий ответ не отвечал на ваш вопрос." in result.answer
    # …and the unsupported world claims do not.
    assert "Ставка по вкладам" not in result.answer
    # The stub does not claim external verification it does not have.
    assert "подтверждено 2" not in result.answer


# ── 6. End to end through AgentLoop ──────────────────────────────────────────

def _loop_with_history(workspace: Path) -> tuple[AgentLoop, list[str], FakeLLM]:
    registry = ToolRegistry()
    llm = FakeLLM(responses=[_SELF_ANALYSIS_ANSWER])
    memory = WorkingMemory()
    memory.record_turn(
        question=PRIOR_QUESTION,
        planner_reasoning="no tools needed",
        tools_used=[],
        artifact_labels=[],
        answer=PRIOR_ANSWER,
    )
    loop = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=workspace / "logs", verbose=False
        ),
        planner=FakePlanner(sources=[], reasoning="объяснимо по истории диалога"),
        memory=memory,
    )
    events: list[str] = []
    _orig = loop.log.log

    def _spy(event_type, *a, **k):
        events.append(event_type)
        return _orig(event_type, *a, **k)

    loop.log.log = _spy  # type: ignore[method-assign]
    return loop, events, llm


def test_end_to_end_self_correction_is_not_suppressed(workspace: Path):
    loop, events, _llm = _loop_with_history(workspace)

    answer = loop.run(ISSUE_119_PROMPT)

    assert "self_analysis_turn" in events
    assert "dialogue_evidence_admitted" in events
    assert "low_evidence_truncation" not in events
    # The substantive analysis reached the operator.
    assert "Мой предыдущий ответ не отвечал на заданный вопрос." in answer
    assert "ни одно утверждение не подтверждено" not in answer
    assert loop.last_verification is not None
    assert loop.last_verification.dialogue_supported_chunks > 0


def test_end_to_end_planner_stays_tool_free(workspace: Path):
    """Acceptance criterion: the planner must not reach for a tool here."""
    loop, _events, _llm = _loop_with_history(workspace)

    loop.run(ISSUE_119_PROMPT)

    dialogue_kinds = {
        ev.kind for ev in (loop.last_provenance.evidences if loop.last_provenance else [])
    }
    assert "session_dialogue" in dialogue_kinds
    assert not (dialogue_kinds - {"session_dialogue", "user_explicit"}), dialogue_kinds
