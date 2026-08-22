"""Banked: procedure retrieval admits an unrelated question on ordinary words.

Measured live 2026-08-20 against the real store of 34 procedures: "what is
the HTTP timeout for the web fetch tool?" came back with three procedures,
the top two being Russian operator sentences about learning to program. Only
a Russian question returned nothing, and for the wrong reason — Russian
tokens are absent from the procedure haystacks, not because the filter
judged relevance.

The mechanism, measured rather than guessed, has two halves:

* `TokenSalience` is built from stored EPISODE QUESTIONS, and its docstring
  says it measures "how rarely the operator says a word". The live corpus of
  200 questions is 125 single-word command names — `self-build-produce` 83
  times, `self-apply-run` 20 — plus a campaign-goal string repeated 10. It is
  the agent's own machine text, not operator speech. An ordinary English word
  the corpus never saw therefore gets the MAXIMUM weight: measured live,
  `tool`, `is` and `what` all score 5.303, exactly what a unique identifier
  like `wombat4412` scores, while `the` — common enough to appear — scores
  1.869.
* `ProceduralStore.search_with_report` admits on `if score:` — any non-zero
  overlap qualifies. There is no threshold, so one maximally-weighted common
  word is enough.

This test rebuilds that condition deterministically rather than reading the
live store, which changes with every run.

Not prescribed: whether the fix is the corpus population, a score threshold,
a stopword floor for unseen tokens, or several. Recorded as MIR-105.
"""
from __future__ import annotations

from core.smart_memory import ProceduralMemoryStore, ProcedureRecord
from core.topic_tokens import build_salience


def _store(tmp_path) -> ProceduralMemoryStore:
    store = ProceduralMemoryStore(tmp_path / "procedural.jsonl")
    store.rewrite(
        [
            ProcedureRecord(
                id="proc_split",
                name="how do I split the wombat4412 module? via file_read, list_dir",
                workflow_key="tools:file_read->list_dir",
                trigger_tags=("file_read", "list_dir", "module", "split"),
                steps=("Situation: a large module.", "Read it, then split it."),
            )
        ]
    )
    return store


def _corpus():
    """A salience corpus shaped like the live one: the agent's own commands."""
    return build_salience(
        ["self-build-produce"] * 80
        + ["self-apply-run"] * 20
        + ["self-task-produce"] * 15
    )


def test_the_relevant_question_finds_it(tmp_path) -> None:
    """Boundary pin: the wire carries a signal at all."""
    found = _store(tmp_path).search_with_report(
        "how do I split the wombat4412 module?", limit=3, salience=_corpus()
    )
    assert [p.id for p in found.procedures] == ["proc_split"]


def test_an_unrelated_question_retrieves_nothing(tmp_path) -> None:
    """CLOSED 2026-08-22 — was a strict xfail from 2026-08-20 (MIR-105).

    What closed it, recorded as the convention requires: not the corpus and
    not a score threshold (both were left unprescribed on purpose), but a
    third mechanism — ELIGIBILITY now requires at least one DISCRIMINATING
    shared token. Scoring is untouched; a match made only of function words
    is simply not a match. One stopword vocabulary now serves both memory
    subsystems (moved to `core/topic_tokens.py`, MIR-008's divergence), and
    it was extended from the live measurement that produced this very gap.
    """
    found = _store(tmp_path).search_with_report(
        "what is the HTTP timeout for the web fetch tool?",
        limit=3,
        salience=_corpus(),
    )
    assert not found.procedures, (
        "an unrelated question pulled "
        f"{[p.name[:40] for p in found.procedures]} into the planner's prompt"
    )
