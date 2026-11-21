"""Citations resolving is not the same as answering the question.

Background: docs/CODE_NOTES.md, "Cited, scored, admitted — and off topic".
"""
from __future__ import annotations

from core.smart_memory import EpisodeRecord, decide_usage_eligibility


def _episode(**over) -> EpisodeRecord:
    base = {
        "goal": "answer the operator",
        "question": "Изучи документацию Python и проверь гипотезу на своей системе",
        "outcome": "success",
        "summary": "документация существует и обновляется",
        "tools_used": ("web_search",),
        "source_labels": ("web:official Python documentation",),
        "verified_chunks": 4,
        "unverified_chunks": 1,
        "answer_quality_score": 0.8,
        "completion_state": "achieved",
    }
    base.update(over)
    return EpisodeRecord(**base)


def test_an_answer_that_missed_the_question_is_not_reusable():
    """Measured live 2026-08-15. Asked to form hypotheses from the Python docs
    and test them against its own system, the agent searched for the phrase
    «official Python documentation» and reported that documentation exists and
    is updated. Its citations resolved — the search really did return pages
    about Python docs — so quality scored 4/5 = 0.8 and the episode was admitted
    as reusable experience, while the same turn measured relevance at 0.32 and
    printed «ответ может отвечать не на заданный вопрос» to the operator.

    One quantity — "a citation resolved" — was buying three different things:
    confidence, episode quality, and admission to experience. The quantity that
    measures whether the answer is about the question bought nothing.
    """
    assert decide_usage_eligibility(_episode(relevance_score=0.321)) is False
