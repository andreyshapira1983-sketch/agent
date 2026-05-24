"""
brain/skills/knowledge.py — Per-profession knowledge base.

Each Profession can carry **static knowledge** (markdown glossaries, style
guides, exemplar deliverables) that the agent loads at startup and tags
into a profession-scoped namespace inside SemanticMemory.

When a Job is matched to a Profession, the runner asks `KnowledgeBase` for
the relevant slice and passes it to ContextBuilder as part of the cycle's
goals/facts so the LLM stops re-discovering the same terminology on every
job.

Layout convention
─────────────────
    professions/
        text_editor.yaml
        text_editor/
            knowledge/
                glossary.md
                style_guide.md
                exemplars/
                    bad_to_good.md
        translator_en_ru/
            knowledge/
                ...

`KnowledgeBase.load_for(profession_id)` walks `<profession_dir>/knowledge/`
and stores each markdown file as one fact (chunked when long) under the
namespace `prof:<profession_id>`.

Design choices
──────────────
- **Idempotent.** Re-loading a profession's knowledge first forgets the
  previous fact-ids, so editing a glossary doesn't double the corpus.
- **Soft on missing dirs.** A profession without a knowledge directory is
  perfectly valid — `load_for` is a no-op.
- **Backend-agnostic.** Talks to SemanticMemory through its public API.
- **Chunk-aware.** Long files are split on blank lines so each chunk fits
  the LLM context window comfortably without losing semantic boundaries.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

logger = logging.getLogger(__name__)


_NAMESPACE_PREFIX = "prof:"
_MAX_CHUNK_CHARS = 1500           # roughly ~350 tokens per chunk
_PARA_SPLIT = re.compile(r"\n\s*\n")


# ════════════════════════════════════════════════════════════════════
# Stored fact identity
# ════════════════════════════════════════════════════════════════════

@dataclass
class KnowledgeFact:
    """One stored knowledge chunk."""

    fact_id:       str
    profession_id: str
    source_file:   str
    text:          str

    @property
    def namespace(self) -> str:
        return f"{_NAMESPACE_PREFIX}{self.profession_id}"


@dataclass
class KnowledgeIndex:
    """Mapping kept in-process so we can `forget` and re-load cleanly."""

    facts_by_profession: dict[str, list[str]] = field(default_factory=dict)

    def add(self, profession_id: str, fact_id: str) -> None:
        self.facts_by_profession.setdefault(profession_id, []).append(fact_id)

    def fact_ids(self, profession_id: str) -> list[str]:
        return list(self.facts_by_profession.get(profession_id, []))

    def clear(self, profession_id: str) -> None:
        self.facts_by_profession.pop(profession_id, None)


# ════════════════════════════════════════════════════════════════════
# KnowledgeBase
# ════════════════════════════════════════════════════════════════════

class KnowledgeBase:
    """Loads per-profession static knowledge into SemanticMemory.

    The KnowledgeBase only adds value when paired with a `SemanticMemory`
    that supports metadata-filtered retrieval; we use the `namespace`
    metadata field to slot facts under a profession and the
    `RetrievalPolicy` to slice them out at query time.
    """

    def __init__(self, semantic_memory) -> None:  # type: ignore[no-untyped-def]
        self._memory = semantic_memory
        self._index = KnowledgeIndex()

    # ────────────────────────────────────────────────────────────────

    def load_for(
        self,
        profession_id: str,
        knowledge_dir: Path | str,
    ) -> list[KnowledgeFact]:
        """Index all markdown files under <knowledge_dir>.

        Returns the freshly-stored facts. If the dir doesn't exist or is
        empty, returns []. Re-loading clears the previous slice for this
        profession first — call this freely on hot-reload.
        """
        kdir = Path(knowledge_dir)
        if not kdir.is_dir():
            logger.info(
                "[KnowledgeBase] no knowledge dir for profession=%s (%s)",
                profession_id, kdir,
            )
            return []

        # Drop the previous slice first so edits don't accumulate.
        self.forget_profession(profession_id)

        stored: list[KnowledgeFact] = []
        for path in sorted(_iter_markdown(kdir)):
            try:
                text = path.read_text(encoding="utf-8")
            except OSError as exc:
                logger.warning("[KnowledgeBase] cannot read %s: %s", path, exc)
                continue
            for chunk in _chunk(text):
                fact_id = self._memory.store_fact(
                    text=chunk,
                    metadata={
                        "namespace":   f"{_NAMESPACE_PREFIX}{profession_id}",
                        "profession":  profession_id,
                        "source_file": str(path.relative_to(kdir.parent)),
                    },
                )
                stored.append(KnowledgeFact(
                    fact_id=fact_id,
                    profession_id=profession_id,
                    source_file=str(path),
                    text=chunk,
                ))
                self._index.add(profession_id, fact_id)
        logger.info(
            "[KnowledgeBase] loaded %d chunks for profession=%s",
            len(stored), profession_id,
        )
        return stored

    def forget_profession(self, profession_id: str) -> int:
        """Drop all facts previously loaded under `prof:<id>`. Returns the count."""
        ids = self._index.fact_ids(profession_id)
        for fact_id in ids:
            try:
                self._memory.forget_fact(fact_id)
            except Exception:  # noqa: BLE001
                logger.exception("[KnowledgeBase] forget_fact failed for %s", fact_id)
        self._index.clear(profession_id)
        return len(ids)

    def fact_count(self, profession_id: str) -> int:
        return len(self._index.fact_ids(profession_id))

    def all_facts(self) -> dict[str, int]:
        """Diagnostic snapshot: profession → loaded chunk count."""
        return {p: len(ids) for p, ids in self._index.facts_by_profession.items()}

    # ────────────────────────────────────────────────────────────────

    def retrieve(
        self,
        profession_id: str,
        query: str,
        top_k: int = 5,
    ) -> list[dict]:
        """Semantic search within one profession's namespace.

        Implementation: query the semantic store, then drop results whose
        metadata.namespace doesn't match. This is a fail-soft strategy —
        it works even when the backend doesn't support metadata-filtered
        queries natively (ChromaDB does, the keyword-fallback does not).
        """
        if not query.strip() or top_k <= 0:
            return []
        # over-fetch then filter, so non-namespaced backends still work
        oversample = max(top_k * 4, top_k + 5)
        results = self._memory.recall(query=query, top_k=oversample)
        target_ns = f"{_NAMESPACE_PREFIX}{profession_id}"
        filtered = []
        for r in results:
            meta = r.get("metadata") or {}
            if meta.get("namespace") == target_ns:
                filtered.append(r)
            if len(filtered) >= top_k:
                break
        return filtered


# ════════════════════════════════════════════════════════════════════
# Helpers
# ════════════════════════════════════════════════════════════════════

def _iter_markdown(root: Path) -> Iterable[Path]:
    for p in root.rglob("*.md"):
        if p.is_file():
            yield p


def _chunk(text: str) -> list[str]:
    """Split a markdown blob into reasonably-sized semantic chunks.

    Strategy:
        - Split on blank lines.
        - Re-merge adjacent paragraphs greedily until next merge would
          exceed `_MAX_CHUNK_CHARS`.
        - Strip trailing whitespace.
    Always returns at least one chunk (or empty list for empty input).
    """
    if not text.strip():
        return []
    paragraphs = [p.strip() for p in _PARA_SPLIT.split(text) if p.strip()]
    chunks: list[str] = []
    buf: list[str] = []
    buf_len = 0
    for para in paragraphs:
        if buf and (buf_len + len(para) + 2) > _MAX_CHUNK_CHARS:
            chunks.append("\n\n".join(buf))
            buf = [para]
            buf_len = len(para)
        else:
            buf.append(para)
            buf_len += len(para) + 2
    if buf:
        chunks.append("\n\n".join(buf))
    return chunks
