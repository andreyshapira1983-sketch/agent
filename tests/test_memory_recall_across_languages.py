"""The operator asks in Russian; memory is written in English. It must still recall.

Two standing project rules meet here: the operator is answered in Russian, and
every repository artefact — including what lands in persistent memory — is
written in English. Retrieval scores by word overlap, so between a Russian
question and an English record the overlap is zero **by construction**.

Measured 2026-08-04 against the agent's own `data/persistent_memory.jsonl`
(47 records, 46 of them Latin-only):

    "кто владеет архитектурой?"          -> 0 records
    "who owns the architecture?"         -> 3 records
    "расскажи про карту репозитория"     -> 0 records
    "tell me about the repository map"   -> 3 records

The same question in the operator's own language returned nothing. A bilingual
token set already existed (`_BROAD_PROJECT_MEMORY_TOKENS`) but only fired for
questions classified as broad self-knowledge — an ordinary Russian question
never reached it.
"""
from __future__ import annotations

from core.bilingual_terms import recall_language_diagnostics
from core.memory_policy import MemoryRetrievalPolicy
from core.models import MemoryRecord


def _record(rid: str, content: str, tags: tuple[str, ...] = ("fact",)) -> MemoryRecord:
    return MemoryRecord(id=rid, content=content, tags=list(tags), source="test")


_STORE = [
    _record("mem_arch", "The architecture doc owns the target levels; README points at it."),
    _record("mem_queue", "The runtime task queue stores tasks as JSONL and claims them exclusively."),
    _record("mem_budget", "The evidence budget trims memory first, then the largest file block."),
    _record("mem_tests", "Targeted tests run on apply; a red run rolls the change back."),
    _record("mem_cat", "Unrelated: the neighbour's cat is called Vasiliy."),
]


def _ids(question: str) -> list[str]:
    return [r.id for r in MemoryRetrievalPolicy().select(_STORE, question)]


def test_a_russian_question_reaches_an_english_record():
    assert "mem_arch" in _ids("кто владеет архитектурой?")


def test_the_russian_and_english_forms_of_one_question_agree():
    """Same meaning, two languages — the recall must not depend on which."""
    assert _ids("расскажи про очередь задач") == _ids("tell me about the task queue")


def test_several_domain_terms_are_reachable_in_russian():
    assert "mem_budget" in _ids("что там с бюджетом улик?")
    assert "mem_tests" in _ids("почему упали тесты при применении патча?")


def test_english_questions_are_unchanged():
    assert "mem_arch" in _ids("who owns the architecture?")
    assert "mem_queue" in _ids("how does the task queue claim work?")


def test_an_unrelated_question_still_matches_nothing():
    """Widening the query must not turn recall into "everything matches"."""
    assert _ids("какая завтра погода в Мадриде?") == []


def test_a_russian_word_does_not_drag_in_an_unrelated_record():
    ids = _ids("расскажи про архитектуру")
    assert "mem_cat" not in ids


# ── the miss counter: decide the next step from numbers, not impressions ────


def test_a_miss_on_a_russian_question_the_table_covers_is_marked_as_such():
    diag = recall_language_diagnostics("что там с бюджетом улик?")

    assert diag["question_script"] == "cyrillic"
    assert diag["bilingual_terms_added"] > 0, "the table should have widened this"


def test_a_miss_the_table_cannot_widen_is_visible():
    """This is the case that would justify translating the question."""
    diag = recall_language_diagnostics("а что там с ёлками во дворе?")

    assert diag["question_script"] == "cyrillic"
    assert diag["bilingual_terms_added"] == 0


def test_an_english_question_is_never_blamed_on_the_language_gap():
    diag = recall_language_diagnostics("who owns the architecture?")

    assert diag["question_script"] == "latin"
    assert diag["bilingual_terms_added"] == 0


def test_a_mixed_question_is_reported_as_mixed():
    diag = recall_language_diagnostics("что говорит README про доктрину?")

    assert diag["question_script"] == "mixed"
    assert diag["bilingual_terms_added"] > 0
