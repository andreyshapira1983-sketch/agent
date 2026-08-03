"""`user_asserted` — the operator's evidence-class ruling (2026-08-03).

«Слова пользователя подтверждают только то, что пользователь это сообщил…
Они не должны автоматически подтверждать объективную истинность содержания.»

The class mirrors `dialogue_supported` structurally (own verdict, own
counter, never `verified`) but NOT in privileges: it earns no credit in the
evidence-support score, it does not join the low-evidence "supported"
numerator, it is not exempt from categorical hedging, and it counts as weak
for episode banking — so an answer built of user-echo citations cannot
score as backed or bank a clean success (the MIR-028 harm).
"""
from __future__ import annotations

from core.evidence import ProvenanceChain, make_evidence
from core.evidence_support import compute_evidence_support
from core.smart_memory import episode_from_agent_cycle
from core.verifier import verify
from core.verifier_patterns import DISCLAIMER_USER_ASSERTED


def _user_only_report():
    return verify(
        answer=(
            "**Facts:**\n"
            "- The operator asked for seventeen times twenty-three [user:current_turn].\n"
            "- A numeric result was requested [user:current_turn].\n"
        ),
        chain=ProvenanceChain(),
        user_question="Compute seventeen times twenty-three and give a numeric result.",
    )


def test_user_echo_earns_no_evidence_support_score():
    """An all-echo answer measures 0.0, not 1.0 — the score inflation that
    made MIR-028 dangerous is gone."""
    report = _user_only_report()
    assert report.user_asserted_chunks == 2
    assert compute_evidence_support(report) == 0.0


def test_user_echo_banks_partial_never_clean_success():
    """Episode banking: user-asserted support counts weak (the loop feeds
    `user_asserted_chunks` into `weak_chunks`), so with zero verified the
    `weak >= verified` guard lands the episode `partial`."""
    episode = episode_from_agent_cycle(
        goal="g",
        question="q",
        answer="a",
        tools_used=["file_read"],
        source_labels=["file:x"],
        verified_chunks=0,
        unverified_chunks=0,
        weak_chunks=2,          # what the loop passes for 2 user_asserted chunks
        declared_completion="achieved",
    )
    assert episode.outcome == "partial"


def test_the_disclaimer_names_the_operator_words_honestly():
    report = _user_only_report()
    assert report.disclaimer == DISCLAIMER_USER_ASSERTED
    assert "[user-asserted:user:current_turn]" in report.annotated_answer


def test_real_evidence_still_verifies_alongside_a_user_citation():
    """A chunk carrying BOTH a user citation and a citation that resolves to
    real gathered evidence is `verified` — the ruling demotes user words, it
    does not poison chunks that also have independent proof."""
    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="file",
        source_id="file:notes.txt",
        obtained_via="file_read",
        claim="notes.txt contents",
        excerpt="the journal has exactly three lines",
    ))
    report = verify(
        answer=(
            "**Facts:**\n"
            "- The journal has exactly three lines [file:notes.txt] [user:current_turn].\n"
        ),
        chain=chain,
        user_question="How many lines does the journal have?",
    )
    assert report.verified_chunks == 1
    assert report.user_asserted_chunks == 0


def test_semantic_support_from_user_words_is_user_asserted_not_verified():
    """The semantic path is LIVE for user evidence: `_find_semantic_support`
    has no kind filter and sorts by confidence (user_explicit = 1.00 first),
    so pre-ruling an unresolvable citation could launder into `verified` via
    an NLI match against the operator's own words. Now it lands
    `user_asserted`. (The structured path, by contrast, is tool_output-only
    by construction and needs no interception.)"""

    class _YesNLI:
        provider = "mock"
        model = "mock-1"

        def complete(self, system, user, **kw):
            return "YES"

    chain = ProvenanceChain()
    chain.add(make_evidence(
        kind="tool_output",
        source_id="tool_output:list_dir",
        obtained_via="list_dir",
        claim="directory listing",
        excerpt="",   # empty excerpt: never matches, exists to keep chain non-empty
    ))
    report = verify(
        answer=(
            "**Facts:**\n"
            "- The server is called Hephaestus [file:nonexistent.txt].\n"
        ),
        chain=chain,
        llm=_YesNLI(),
        user_question="Our server is called Hephaestus and lives in Oslo.",
    )
    assert report.verified_chunks == 0
    assert report.user_asserted_chunks == 1


def test_categorical_user_asserted_claims_are_still_hedged():
    """The ruling's honesty edge, pinned at the consumer: `user_asserted` is
    deliberately NOT in the hedging exempt set — a categorical world claim
    supported only by the operator's words still counts as unsupported for
    `core.unsupported_claims`."""
    from core.unsupported_claims import _count_categorical_unsupported

    report = verify(
        answer=(
            "**Facts:**\n"
            "- Наш сервер всегда доступен без исключений [user:current_turn].\n"
        ),
        chain=ProvenanceChain(),
        user_question="Наш сервер всегда доступен без исключений.",
    )
    assert report.user_asserted_chunks == 1
    assert _count_categorical_unsupported(report) == 1


def test_the_loop_feeds_user_asserted_into_episode_weak_chunks(tmp_path):
    """Integration: the loop's weak_chunks assembly includes
    `user_asserted_chunks`, so the banked episode cannot read as a clean
    success (captured at the `_record_experience_memory` seam)."""
    from pathlib import Path

    from core.logger import TraceLogger
    from core.loop import AgentLoop
    from core.memory import WorkingMemory
    from core.planner import LLMPlanner
    from core.policy import PolicyGate
    from tools.base import ToolRegistry

    answer = (
        "Conclusion: the operator asked about the server [user:current_turn].\n"
        "Sources:\n1. [user:current_turn]\nConfidence: low\nUnverified: nothing"
    )

    class _EchoLLM:
        provider = "mock"
        model = "mock-1"

        def complete(self, system, user, **kw):
            return answer

        def stream(self, system, user, **kw):
            yield answer

    llm = _EchoLLM()
    registry = ToolRegistry()
    logger = TraceLogger(trace_id="trace_ua_weak", log_dir=Path(tmp_path), verbose=False)
    agent = AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=llm,
        logger=logger,
        planner=LLMPlanner(llm=llm, registry=registry),
        memory=WorkingMemory(),
    )
    captured: dict = {}
    original = agent._record_experience_memory

    def spy(**kwargs):
        captured.update(kwargs)
        return original(**kwargs)

    agent._record_experience_memory = spy  # type: ignore[method-assign]
    agent.run("Расскажи, о чём я спросил про сервер.")

    assert agent.last_verification is not None
    ua = agent.last_verification.user_asserted_chunks
    assert ua >= 1, "precondition: the echo answer must produce user_asserted chunks"
    assert captured["weak_chunks"] >= ua


def test_dialogue_supported_is_untouched_by_the_new_class():
    """The #119 class keeps its own lane: a dialogue-scoped claim backed by
    the session transcript stays `dialogue_supported`, not `user_asserted`."""
    from core.evidence import evidence_from_prior_turn

    chain = ProvenanceChain()
    chain.add(evidence_from_prior_turn(
        turn_id="t1",
        turn_index=1,
        question="ранее заданный вопрос",
        answer="мой предыдущий ответ был неполон",
    ))
    report = verify(
        answer=(
            "**Conclusion:**\n"
            "Мой предыдущий ответ в этой сессии был неполон.\n"
        ),
        chain=chain,
        user_question="Объясни свой предыдущий ответ.",
        expects_contract_headers=False,
    )
    assert report.dialogue_supported_chunks >= 1
    assert report.user_asserted_chunks == 0
