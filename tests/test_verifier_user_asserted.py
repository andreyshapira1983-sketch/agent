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
from core.verifier import verify


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
