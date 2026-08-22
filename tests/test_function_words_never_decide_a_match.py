"""A match made of function words is not a match — MIR-105 / 024 / 008.

ONE ROOT, THREE ENTRIES. Measured 2026-08-22:

* MIR-105 — a query of pure function words («что мне делать с workflow using
  вот это всё») retrieved **17 of 34** procedures; the salience corpus cannot
  fix it, because it can weight only 130 of the 587 words in the procedure
  vocabulary (22%) and the rest all collapse to the identical maximum
  `unseen` weight;
* MIR-024 — «Я хочу ЗАПУСТИТЬ АГЕНТА НА НЕДЕЛЮ, что мне сделать?» and «Я хочу
  УДАЛИТЬ ВСЕ ЛОГИ, что мне сделать?» score Jaccard 0.400 — the threshold —
  and the second is annotated to the planner as a repeat of the first. A
  launch and a deletion, classified as one question;
* MIR-008 — the episodic tokenizer keeps stopwords while the persistent one
  drops them; the divergence itself was never shown to cause a wrong result,
  and the harm hunt landed exactly here, at what the stopword-keeping side
  feeds.

THE RULE. Tokenization is unchanged — compound names, digit tokens and the
rest keep working, and no consumer's vocabulary shifts underneath it. What
changes is the DECISION: two texts are related only if they share at least one
DISCRIMINATING token, and the stopword list that answers "discriminating" is
the one this repository already wrote (`core/memory_policy._STOPWORDS`), moved
beside the tokenizer so both sides read one vocabulary instead of two.

WHAT THIS DOES NOT DO. It does not add a score threshold (MIR-105 leaves that
choice open on purpose — a threshold over a corpus that cannot discriminate
would be a number chosen to look right). It does not touch the retrieval
ORDER, only whether an item is eligible at all.
"""
from __future__ import annotations

from pathlib import Path

from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
)
from core.topic_tokens import discriminating_tokens

_FILLER_RU = "что мне делать с этим вот это всё как у тебя"


def test_a_function_word_only_query_has_no_discriminating_tokens() -> None:
    assert discriminating_tokens(_FILLER_RU) == set()
    assert discriminating_tokens("what is it that you do about this") == set()


def test_content_words_survive() -> None:
    tokens = discriminating_tokens("что делает core/smart_memory.py при вытеснении")
    assert "smart_memory" in " ".join(tokens) or "core/smart_memory.py" in tokens
    assert "вытеснении" in tokens
    assert "что" not in tokens


def test_a_procedure_is_not_retrieved_by_function_words(workspace: Path) -> None:
    """MIR-105's live shape: 23 of 34 procedure names are operator sentences,
    so Russian filler matched them by «вот» / «что» / «мне»."""
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    store.rewrite([
        ProcedureRecord(
            id="p1", name="вот я тебе говорю сейчас что надо обучиться",
            workflow_key="tools:x", trigger_tags=(), steps=("Run tool",),
            source_episode_ids=(), success_count=2, failure_count=0,
            confidence=0.7, status="active",
        ),
    ])

    assert store.search(_FILLER_RU, limit=3) == [], (
        "a query of pure filler retrieved a procedure — the planner is being "
        "handed unrelated experience as if it were relevant"
    )


def test_a_procedure_is_still_retrieved_by_a_real_word(workspace: Path) -> None:
    store = ProceduralMemoryStore(workspace / "procedures.jsonl")
    store.rewrite([
        ProcedureRecord(
            id="p1", name="Workflow using read_logs",
            workflow_key="tools:read_logs", trigger_tags=(), steps=("read_logs",),
            source_episode_ids=(), success_count=2, failure_count=0,
            confidence=0.7, status="active",
        ),
    ])

    assert [p.id for p in store.search("посмотри read_logs", limit=3)] == ["p1"]


def test_an_episode_is_not_retrieved_by_function_words(workspace: Path) -> None:
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(EpisodeRecord(
        goal="g", question="Я хочу запустить агента на неделю, что мне сделать?",
        outcome="success", summary="launched", tags=("lesson",),
    ))

    assert store.search(_FILLER_RU, limit=3) == []


def test_opposite_intents_sharing_a_frame_are_not_the_same_question(
    workspace: Path,
) -> None:
    """MIR-024's exact pair: a launch and a deletion scored 0.400 — the repeat
    threshold — because the frame «Я хочу … что мне сделать?» is most of the
    overlap."""
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(EpisodeRecord(
        goal="g", question="Я хочу запустить агента на неделю, что мне сделать?",
        outcome="success", summary="s", tags=(),
    ))

    match, score = store.find_most_similar(
        "Я хочу удалить все логи, что мне сделать?", threshold=0.40
    )
    assert match is None, (
        f"a deletion was treated as a repeat of a launch (score {score:.2f}) — "
        "the frame is not the question"
    )


def test_the_same_question_is_still_a_repeat(workspace: Path) -> None:
    """The boundary: re-ask detection must keep working on real repeats."""
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(EpisodeRecord(
        goal="g", question="Я хочу удалить все логи, что мне сделать?",
        outcome="success", summary="s", tags=(),
    ))

    match, score = store.find_most_similar(
        "Я хочу удалить все логи — что мне сделать?", threshold=0.40
    )
    assert match is not None and score >= 0.40


# ── The field's own counterexample, tested against this repair ───────────────
#
# Hardcoded stoplists are criticised in IR precisely because they invert
# meaning: "movies WITHOUT nicolas cage" becomes its opposite once the
# function word is dropped, and modern practice prefers IDF, which downweights
# without deleting. That criticism was taken to THIS code rather than argued
# with — and it landed. Two defects, one pre-existing and made consequential
# by this repair, one older and never noticed:
#
#   * "не" sat in the inherited stopword list, so «почему тест падает» and
#     «почему тест НЕ падает» reduced to the same set;
#   * worse, `topic_tokens` never produced "не" at all — the CORE-10 length
#     rule (>2 chars, digits excepted) had been deleting short Russian
#     negation SILENTLY since it was written.
#
# Polarity words are now a third exception to the length rule and are exempt
# from the stoplist. IDF is not available as the field's preferred answer
# here: measured 2026-08-22, the corpus can weight only 22% of the procedure
# vocabulary and the rest collapse to one maximum value.

def test_negation_is_never_filler() -> None:
    from core.topic_tokens import discriminating_tokens

    for positive, negative in (
        ("почему тест падает", "почему тест не падает"),
        ("покажи логи", "покажи логи без ошибок"),
        ("movies with nicolas cage", "movies without nicolas cage"),
        ("запусти это", "не запусти это"),
    ):
        assert discriminating_tokens(positive) != discriminating_tokens(negative), (
            f"{positive!r} and {negative!r} reduce to the same words — the "
            "repair inverted a meaning, which is the exact failure the field "
            "names against hardcoded stoplists"
        )


def test_the_shared_tokenizer_is_not_changed_for_one_matcher() -> None:
    """The boundary this repair crossed once and was caught crossing.

    The first attempt added polarity as a third exception to the tokenizer's
    length rule — changing the vocabulary EVERY consumer reads, and breaking
    `test_a_comma_is_not_a_letter`, which pins CORE-10 deliberately. The suite
    caught it; the repair moved to the decision layer instead, where only the
    matcher's view changes. Polarity is added to `discriminating_tokens`
    straight from the text, so `topic_tokens` stays byte-identical.
    """
    from core.topic_tokens import discriminating_tokens, topic_tokens

    assert "не" not in topic_tokens("не так"), "CORE-10 was changed again"
    assert "v2" in topic_tokens("версия v2")
    assert "не" in discriminating_tokens("почему тест не падает")
