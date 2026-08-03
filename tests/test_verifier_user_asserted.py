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
