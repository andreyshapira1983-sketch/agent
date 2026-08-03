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
from core.evidence_classes import (
    classify_evidence,
    is_dialogue_scoped_claim,
    is_self_analysis_turn,
)
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.low_evidence_policy import evaluate_low_evidence_policy
from core.memory import WorkingMemory
from core.policy import PolicyGate
from core.smart_memory import episode_from_agent_cycle
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


# ── 1. Turn classification ───────────────────────────────────────────────────

def test_issue_119_prompt_is_recognised_as_self_analysis():
    decision = is_self_analysis_turn(ISSUE_119_PROMPT, has_prior_turn=True)
    assert decision.is_self_analysis
    assert decision.reason == "conversational_correction"


def test_self_analysis_needs_a_prior_turn():
    """No prior turn → nothing to analyse, and no transcript to lean on."""
    decision = is_self_analysis_turn(ISSUE_119_PROMPT, has_prior_turn=False)
    assert not decision.is_self_analysis
    assert decision.reason == "no_prior_turn"


def test_criticism_of_something_else_is_not_self_analysis():
    """'Why is the server's response incorrect?' is a world question."""
    decision = is_self_analysis_turn(
        "Почему ответ сервера некорректен?", has_prior_turn=True
    )
    assert not decision.is_self_analysis
    assert decision.reason == "agent_not_addressed"


def test_ordinary_question_is_not_self_analysis():
    decision = is_self_analysis_turn(
        "Расскажи, что ты умеешь делать с файлами", has_prior_turn=True
    )
    assert not decision.is_self_analysis


# ── 2. Claim scoping ─────────────────────────────────────────────────────────

def test_claims_about_the_exchange_are_dialogue_scoped():
    for claim in (
        "Мой предыдущий ответ не отвечал на ваш вопрос.",
        "Я перечислил общие рекомендации вместо ответа по существу.",
        "You asked about capabilities and my reply listed generic advice.",
    ):
        assert is_dialogue_scoped_claim(claim), claim


def test_world_claims_in_first_person_are_not_dialogue_scoped():
    """A person marker alone must never buy dialogue support."""
    for claim in (
        "Я рекомендую купить облигации по 100 рублей.",
        "Биткоин стоит 50000 долларов.",
        "I think Python 3.14 was released in October.",
    ):
        assert not is_dialogue_scoped_claim(claim), claim


def test_prior_turn_evidence_is_classified_as_session_dialogue():
    ev = _dialogue_chain().evidences[0]
    assert classify_evidence(ev) == "session_dialogue"
    assert PRIOR_ANSWER.strip() in ev.excerpt


# ── 3. Verifier verdicts ─────────────────────────────────────────────────────

_SELF_ANALYSIS_ANSWER = "\n".join(
    [
        "Conclusion:",
        "Мой предыдущий ответ не отвечал на заданный вопрос.",
        "Facts:",
        "- Вы спросили про возможности, а я ответил общими рекомендациями.",
        "- Я не привёл ни одного конкретного примера в том ответе.",
        "- Мой ответ был построен как список советов, а не как ответ.",
        "- Я не уточнил, о каком именно контексте идёт речь в вопросе.",
        "- Вопрос требовал разбора, а я выдал шаблонную реплику.",
        "- Мой ответ не опирался ни на один источник этого цикла.",
        "Sources: dialogue",
        "Confidence: low",
        "Unverified: nothing",
        "Safety: ok",
    ]
)


def test_dialogue_scoped_claims_become_dialogue_supported_not_unverified():
    report = verify(answer=_SELF_ANALYSIS_ANSWER, chain=_dialogue_chain())
    assert report.dialogue_supported_chunks >= 6, report.to_log_payload()
    # Every sentence of the analysis itself is supported; only the Output
    # Contract tail ("Confidence: low", "Safety: ok", …) is left over.
    analysis = [
        c for c in report.chunks
        if "ответ" in c.text.lower() or "вопрос" in c.text.lower()
    ]
    assert analysis
    assert all(c.verdict == "dialogue_supported" for c in analysis), [
        (c.text, c.verdict) for c in analysis
    ]


def test_dialogue_support_is_never_promoted_to_verified():
    report = verify(answer=_SELF_ANALYSIS_ANSWER, chain=_dialogue_chain())
    assert report.verified_chunks == 0
    assert all(c.verdict != "verified" for c in report.chunks)


def test_dialogue_citation_on_a_world_claim_earns_no_support():
    """The transcript proves what was said — never an arbitrary outside fact."""
    answer = "\n".join(
        [
            "Conclusion:",
            "Ставка ЦБ составляет 21% годовых [dialogue:turn_1].",
            "Facts:",
            "- Инфляция в прошлом месяце была 0.8% [dialogue:turn_1].",
        ]
    )
    report = verify(answer=answer, chain=_dialogue_chain())
    assert report.verified_chunks == 0
    assert report.dialogue_supported_chunks == 0
    assert all(c.verdict != "dialogue_supported" for c in report.chunks)


def test_without_dialogue_evidence_the_defect_still_reproduces():
    """The pre-fix condition, kept in the suite as the other half of the delta.

    Measured on ``main`` @ 3c761c5 with this exact answer and prompt:
    ``verified=0, unverified=11``, the gate triggers, 69 characters are
    suppressed and the operator receives "ни одно утверждение не подтверждено
    источниками этого цикла". Reproduced here by withholding the transcript,
    which is precisely what the old loop did — it never admitted one.
    """
    report = verify(answer=_SELF_ANALYSIS_ANSWER, chain=ProvenanceChain())
    assert report.dialogue_supported_chunks == 0
    assert report.unverified_chunks > 0

    result = evaluate_low_evidence_policy(
        answer=_SELF_ANALYSIS_ANSWER,
        report=report,
        question=ISSUE_119_PROMPT,
        evidence_expected=True,
    )
    assert result.triggered
    assert "Мой предыдущий ответ не отвечал" not in result.answer


# ── 4. The truncation gate ───────────────────────────────────────────────────

def test_self_analysis_answer_is_not_truncated():
    """The defect itself: 10 chunks, 0 verified → the analysis was deleted."""
    report = verify(answer=_SELF_ANALYSIS_ANSWER, chain=_dialogue_chain())
    result = evaluate_low_evidence_policy(
        answer=_SELF_ANALYSIS_ANSWER,
        report=report,
        question=ISSUE_119_PROMPT,
    )
    assert not result.triggered, result.to_log_payload()
    assert result.answer == _SELF_ANALYSIS_ANSWER
    assert result.suppressed_chars == 0


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


def test_purely_unsupported_answer_still_truncates():
    """Protective: the gate must keep firing where it always did."""
    junk = "\n".join(
        ["Conclusion:"] + [f"Ставка по вкладам сейчас {i} процентов." for i in range(10)]
    )
    report = verify(answer=junk, chain=_dialogue_chain())
    result = evaluate_low_evidence_policy(
        answer=junk, report=report, question=ISSUE_119_PROMPT
    )
    assert result.triggered
    assert result.answer != junk
    assert "Ставка по вкладам" not in result.answer


# ── 5. Memory admission ──────────────────────────────────────────────────────

def test_self_analysis_episode_is_not_banked_as_clean_success():
    """Acceptance criterion: no false promotion into procedural memory.

    ``verified=0, unverified=0`` alone scores a perfect run (MIR-002). The
    dialogue-supported mass is carried as ``weak_chunks`` precisely so this
    episode lands as ``partial``.
    """
    episode = episode_from_agent_cycle(
        goal="explain the previous answer",
        question=ISSUE_119_PROMPT,
        answer=_SELF_ANALYSIS_ANSWER,
        tools_used=[],
        source_labels=["general-knowledge"],
        verified_chunks=0,
        unverified_chunks=0,
        weak_chunks=6,  # what core/loop.py passes for dialogue_supported
    )
    assert episode.outcome == "partial"


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


def test_end_to_end_ordinary_question_admits_no_dialogue_evidence(workspace: Path):
    """Protective: dialogue evidence is admitted only on a correction turn."""
    loop, events, _llm = _loop_with_history(workspace)

    loop.run("расскажи, сколько сейчас стоит биткоин")

    assert "dialogue_evidence_admitted" not in events
    kinds = {
        ev.kind for ev in (loop.last_provenance.evidences if loop.last_provenance else [])
    }
    assert "session_dialogue" not in kinds
