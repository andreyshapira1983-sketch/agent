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
