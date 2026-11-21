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
)

_FILLER_RU = "что мне делать с этим вот это всё как у тебя"


def test_an_episode_is_not_retrieved_by_function_words(workspace: Path) -> None:
    store = EpisodicMemoryStore(workspace / "ep.jsonl")
    store.save(EpisodeRecord(
        goal="g", question="Я хочу запустить агента на неделю, что мне сделать?",
        outcome="success", summary="launched", tags=("lesson",),
    ))

    assert store.search(_FILLER_RU, limit=3) == []
