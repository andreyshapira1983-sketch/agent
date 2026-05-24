"""
brain/context_builder.py — Context Assembly

The Brain decides WHAT the LLM sees.
LLM receives only what the Brain chooses to give it.
This is a critical control point: garbage in → garbage out.

Privacy:
    When a `PIIRedactor` is supplied, every string that crosses into the
    context dict (input, history messages, facts) is passed through
    redact() first. The LLM therefore never sees raw emails, phones,
    cards, etc. — only stable tokens like `[EMAIL_1]`. The redactor
    keeps a per-session mapping so the Brain can call `restore()` on
    its final response to the user.

S1.3 additions:
    - similar_episodes: top-K semantically similar past episodes from
      EpisodicMemory, injected as extra context so the LLM can see
      "I've handled this before".
    - recency_weight: when > 0, facts are re-ranked mixing semantic
      score and freshness (ts metadata). Older facts score lower.
    - include_anti_patterns: when True, anti-pattern facts (namespace
      "antipatterns:<profession_id>") are allowed through the filter.
    - _meta: diagnostics dict in context (chars_used, fact_sources).
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING

from .interfaces.memory_interface import MemoryInterface
from .memory.retrieval_policy import DEFAULT_POLICY, RetrievalPolicy
from .privacy import PIIRedactor

if TYPE_CHECKING:
    from .skills.knowledge import KnowledgeBase

logger = logging.getLogger(__name__)

MAX_CONTEXT_TOKENS = 8000
_CHARS_PER_TOKEN   = 4
MAX_CONTEXT_CHARS  = MAX_CONTEXT_TOKENS * _CHARS_PER_TOKEN

_INPUT_RESERVE   = 0.30
_HISTORY_RESERVE = 0.40
_FACTS_RESERVE   = 0.30

# Max age (seconds) used for recency normalisation — facts older than
# this window get recency_score = 0.
_RECENCY_WINDOW = 30 * 24 * 3600  # 30 days


class ContextBuilder:
    """
    Assembles the context dict that gets passed to the LLM.
    The Brain controls what goes in here.
    """

    def __init__(
        self,
        memory: MemoryInterface,
        redactor: PIIRedactor | None = None,
        retrieval_policy: RetrievalPolicy | None = None,
        knowledge_base: "KnowledgeBase | None" = None,
    ) -> None:
        self._memory = memory
        self._redactor = redactor
        self._policy = retrieval_policy or DEFAULT_POLICY
        self._knowledge = knowledge_base

    def build(
        self,
        raw_input: str,
        goals: list[dict],
        session_id: str,
        *,
        policy: RetrievalPolicy | None = None,
        system_prompt: str | None = None,
    ) -> dict:
        """
        Build a structured context object.

        Returns:
            {
                "input": str,
                "goals": [...],
                "history": [...],
                "facts": [...],
                "session_id": str,
            }

        When a redactor is configured, `input`, `history[*].content`, and
        `facts[*].text` are scrubbed of PII before being placed in the
        returned dict. Memory itself remains unchanged — encryption at rest
        is the job of the memory backend (see EpisodicMemory + SQLCipher).

        Args:
            policy: optional one-shot RetrievalPolicy that overrides the
                builder's default for this call. Used by the WorkflowRunner
                to inject a profession-scoped policy.
        """
        active = policy or self._policy

        history = self._memory.recall_history(
            session_id=session_id, limit=active.history_limit,
        )

        # ── Facts retrieval split: profession-scoped + universal ────
        facts: list[dict] = []
        if self._knowledge is not None and active.profession_namespace:
            profession_id = active.profession_namespace.removeprefix("prof:")
            reserved = active.reserved_profession_slots()
            if reserved > 0:
                facts.extend(self._knowledge.retrieve(
                    profession_id=profession_id,
                    query=raw_input,
                    top_k=reserved,
                ))
        remaining = max(0, active.facts_top_k - len(facts))
        if remaining > 0:
            universal = self._memory.recall_facts(query=raw_input, top_k=remaining)
            # When a profession namespace is set, filter by allowed namespaces.
            # S1.3: also allow anti-pattern namespace when include_anti_patterns=True.
            if active.profession_namespace:
                allowed_ns: set[str | None] = {None, active.profession_namespace}
                if active.include_anti_patterns:
                    anti_ns = "antipatterns:" + active.profession_namespace.removeprefix("prof:")
                    allowed_ns.add(anti_ns)
                universal = [
                    f for f in universal
                    if (f.get("metadata") or {}).get("namespace") in allowed_ns
                ]
            facts.extend(universal)

        if active.similarity_threshold > 0.0:
            facts = [
                f for f in facts
                if float(f.get("score", 0.0)) >= active.similarity_threshold
            ]
        facts = facts[: active.facts_top_k]

        # ── S1.3: recency reranking ──────────────────────────────────
        if active.recency_weight > 0.0 and facts:
            facts = _rerank_by_recency(facts, active.recency_weight)

        # ── S1.3: similar past episodes ─────────────────────────────
        similar_episodes: list[dict] = []
        if active.similar_episodes_top_k > 0:
            try:
                similar_episodes = self._memory.recall_similar(
                    raw_input, top_k=active.similar_episodes_top_k,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[ContextBuilder] recall_similar skipped: %s", exc)

        # ── PII redaction ────────────────────────────────────────────
        redactor = self._redactor
        if redactor is not None:
            safe_input = redactor.redact(raw_input, session_id=session_id)
            history = [
                {**m, "content": redactor.redact(
                    str(m.get("content", "")), session_id=session_id,
                )}
                for m in history
            ]
            facts = [
                {**f, "text": redactor.redact(
                    str(f.get("text", "")), session_id=session_id,
                )}
                for f in facts
            ]
            similar_episodes = [
                {**ep, "text": redactor.redact(
                    str(ep.get("text", "")), session_id=session_id,
                )}
                for ep in similar_episodes
            ]
        else:
            safe_input = raw_input

        history, facts = _trim_to_budget(safe_input, history, facts)

        # ── Assemble context ─────────────────────────────────────────
        context: dict = {
            "input": safe_input,
            "goals": goals,
            "history": history,
            "facts": facts,
            "session_id": session_id,
        }
        if similar_episodes:
            context["similar_episodes"] = similar_episodes
        if system_prompt and system_prompt.strip():
            context["system_prompt"] = system_prompt.strip()

        # ── S1.3: diagnostics meta ───────────────────────────────────
        chars_used = (
            len(safe_input)
            + sum(len(str(m.get("content", ""))) for m in history)
            + sum(len(str(f.get("text", ""))) for f in facts)
        )
        fact_sources = list({
            (f.get("metadata") or {}).get("source", "")
            for f in facts
            if (f.get("metadata") or {}).get("source")
        })
        context["_meta"] = {
            "chars_used": chars_used,
            "tokens_approx": chars_used // 4,
            "similar_episodes_count": len(similar_episodes),
            "fact_sources": fact_sources,
        }

        logger.debug(
            "[ContextBuilder] Built context | goals=%d history=%d facts=%d "
            "similar_eps=%d redacted=%s profession=%s chars=%d",
            len(goals), len(history), len(facts),
            len(similar_episodes),
            redactor is not None,
            active.profession_namespace or "—",
            chars_used,
        )

        return context


# ════════════════════════════════════════════════════════════════════
# Budget enforcement
# ════════════════════════════════════════════════════════════════════

def _trim_to_budget(
    safe_input: str,
    history: list[dict],
    facts: list[dict],
) -> tuple[list[dict], list[dict]]:
    """Drop oldest history rows and lowest-ranked facts until under cap.

    `safe_input` is sacred — it is never trimmed. We use it only as a
    cost subtracted from the total budget. History is trimmed from the
    front (oldest first); facts are trimmed from the end (lowest score
    first, since callers already sorted them by relevance).
    """
    input_cost = len(safe_input or "")
    remaining = max(0, MAX_CONTEXT_CHARS - input_cost)
    if remaining == 0:
        # Pathological input larger than the whole budget — keep at
        # least one history row and one fact for grounding.
        return history[-1:], facts[:1]

    history_cap = int(remaining * (_HISTORY_RESERVE / (_HISTORY_RESERVE + _FACTS_RESERVE)))
    facts_cap   = remaining - history_cap

    trimmed_history = _trim_messages(history, history_cap, keep_recent=True)
    trimmed_facts   = _trim_messages(facts,   facts_cap,   keep_recent=False)

    if trimmed_history is not history or trimmed_facts is not facts:
        logger.info(
            "[ContextBuilder] trimmed: history %d→%d facts %d→%d (budget=%dch)",
            len(history), len(trimmed_history),
            len(facts), len(trimmed_facts),
            MAX_CONTEXT_CHARS,
        )
    return trimmed_history, trimmed_facts


def _trim_messages(items: list[dict], char_cap: int, *, keep_recent: bool) -> list[dict]:
    """Pick the largest prefix/suffix of `items` whose total length ≤ char_cap.

    `keep_recent=True` keeps the tail (latest history); False keeps the
    head (highest-ranked facts).
    """
    if not items or char_cap <= 0:
        return [] if char_cap <= 0 else list(items)
    indices = range(len(items) - 1, -1, -1) if keep_recent else range(len(items))
    picked: list[int] = []
    used = 0
    for idx in indices:
        item = items[idx]
        size = _length(item)
        if used + size > char_cap and picked:
            break
        picked.append(idx)
        used += size
    if not picked:
        return [items[indices[0]]] if indices else []   # always keep at least one
    picked.sort()
    return [items[i] for i in picked]


def _length(item: dict) -> int:
    body = item.get("content") or item.get("text") or ""
    return len(str(body))


# ════════════════════════════════════════════════════════════════════
# S1.3 — Recency reranking
# ════════════════════════════════════════════════════════════════════

def _rerank_by_recency(facts: list[dict], recency_weight: float) -> list[dict]:
    """
    Re-rank facts by mixing semantic similarity score with freshness.

    final_score = (1 - w) * semantic_score + w * recency_score

    recency_score = 1.0 for brand-new facts, 0.0 for facts older than
    _RECENCY_WINDOW. Facts without a `ts` metadata field keep their
    original semantic score unchanged.
    """
    now = time.time()
    reranked = []
    for f in facts:
        ts = (f.get("metadata") or {}).get("ts")
        sem = float(f.get("score", 0.5))
        if ts is not None:
            age = max(0.0, now - float(ts))
            recency = max(0.0, 1.0 - age / _RECENCY_WINDOW)
            new_score = round((1.0 - recency_weight) * sem + recency_weight * recency, 4)
            reranked.append({**f, "score": new_score})
        else:
            reranked.append(f)
    reranked.sort(key=lambda x: float(x.get("score", 0.0)), reverse=True)
    return reranked
