"""
brain/memory/retrieval_policy.py — Declarative recall policy.

`ContextBuilder` historically used hardcoded `top_k=5` for facts and
`limit=10` for history. That worked for a single chat session, but for a
freelancer agent juggling many professions, each with its own glossary
and style guide, we need finer control: how many facts? from which
profession namespace? do recent jobs outweigh universal knowledge?

`RetrievalPolicy` captures these knobs as data so:
    - the Brain default stays predictable,
    - each Profession may carry its own `retrieval_policy` in YAML
      that the runner injects per cycle,
    - tests can pin the policy to a fixed shape.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RetrievalPolicy:
    """How aggressive ContextBuilder should be when populating the LLM context.

    Fields:
        history_limit:
            Max number of recent messages to inject from working memory.

        facts_top_k:
            Total fact slots (universal + per-profession combined) that
            ContextBuilder may fill.

        profession_namespace:
            When set, ContextBuilder asks `KnowledgeBase.retrieve` for
            facts in this namespace BEFORE falling back to the universal
            semantic store.

        profession_facts_top_k:
            How many of `facts_top_k` slots are reserved for the
            profession's knowledge. Defaults to all of them when a
            profession namespace is set.

        similarity_threshold:
            Drop any retrieved fact whose score is below this. 0.0
            disables. Useful when the embedder produces a long tail of
            near-irrelevant hits.

        recency_weight:
            0..1. When >0, ContextBuilder rescores facts by mixing
            `score` and recency (right now this is metadata-based; the
            backend must record `ts` on stored facts). Recency weight 0
            means pure semantic ranking.

        include_anti_patterns:
            When True, also pull from `antipatterns:<profession_id>`
            namespace so the LLM sees what NOT to do. Off by default —
            anti-patterns can confuse the model in straightforward
            tasks.
    """

    history_limit:            int = 10
    facts_top_k:              int = 5
    profession_namespace:     str | None = None
    profession_facts_top_k:   int | None = None
    similarity_threshold:     float = 0.0
    recency_weight:           float = 0.0
    include_anti_patterns:    bool = False
    similar_episodes_top_k:   int = 3

    # ────────────────────────────────────────────────────────────────

    def with_profession(self, profession_id: str) -> "RetrievalPolicy":
        """Return a copy specialised for a given profession."""
        return RetrievalPolicy(
            history_limit=self.history_limit,
            facts_top_k=self.facts_top_k,
            profession_namespace=f"prof:{profession_id}",
            profession_facts_top_k=self.profession_facts_top_k,
            similarity_threshold=self.similarity_threshold,
            recency_weight=self.recency_weight,
            include_anti_patterns=self.include_anti_patterns,
            similar_episodes_top_k=self.similar_episodes_top_k,
        )

    def reserved_profession_slots(self) -> int:
        """How many of `facts_top_k` go to the profession namespace."""
        if self.profession_namespace is None:
            return 0
        if self.profession_facts_top_k is None:
            return self.facts_top_k
        return max(0, min(self.profession_facts_top_k, self.facts_top_k))

    def universal_slots(self) -> int:
        return max(0, self.facts_top_k - self.reserved_profession_slots())


DEFAULT_POLICY = RetrievalPolicy()
