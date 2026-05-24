"""
brain/memory/memory_manager.py — Memory Manager

The Brain talks ONLY to MemoryManager.
MemoryManager routes to the right memory type.

Brain does not know about Working/Episodic/Semantic/Procedural internals.
MemoryManager implements MemoryInterface — this is what Brain receives.

Flow:
    Brain.think()
        → ContextBuilder.build()
            → MemoryManager.recall_history()   → WorkingMemory (fast)
                                                → EpisodicMemory (if working empty)
            → MemoryManager.recall_facts()     → SemanticMemory

    Brain.think() result
        → MemoryManager.store()               → WorkingMemory + EpisodicMemory

S3.4:
        → MemoryManager.match_skill()         → ProceduralMemory (skill lookup)
        → MemoryManager.save_trajectory()     → ProceduralMemory (record success)
"""

from __future__ import annotations

import logging

from ..interfaces.memory_interface import MemoryInterface
from .working_memory import WorkingMemory
from .episodic_memory import EpisodicMemory
from .semantic_memory import SemanticMemory
from .procedural_memory import ProceduralMemory

logger = logging.getLogger(__name__)


class MemoryManager(MemoryInterface):
    """
    Single entry point for all memory operations.
    Implements MemoryInterface — plug directly into Brain.
    """

    def __init__(
        self,
        episodic_db: str = "data/episodic.db",
        semantic_dir: str = "data/semantic",
        working_limit: int = 20,
        procedural_db: str = "data/procedural.db",
        procedural_chroma_dir: str = "data/procedural_chroma",
    ) -> None:
        self._working = WorkingMemory(limit=working_limit)
        self._episodic = EpisodicMemory(db_path=episodic_db)
        self._semantic = SemanticMemory(persist_dir=semantic_dir)
        self._procedural = ProceduralMemory(
            db_path=procedural_db,
            chroma_dir=procedural_chroma_dir,
        )
        logger.info("[MemoryManager] All memory systems initialized (including ProceduralMemory)")

    # ------------------------------------------------------------------
    # MemoryInterface implementation
    # ------------------------------------------------------------------

    def recall_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """
        Brain calls this to get conversation history.
        Priority: WorkingMemory first (fast), then EpisodicMemory (persistent).
        """
        working = self._working.recall(session_id=session_id, limit=limit)
        if working:
            return working

        # Working memory empty (e.g. after restart) — fall back to episodic
        logger.debug("[MemoryManager] Working memory empty, loading from episodic")
        episodic = self._episodic.recall(session_id=session_id, limit=limit)

        # Warm up working memory from episodic
        for msg in episodic:
            self._working.store(
                session_id=session_id,
                role=msg["role"],
                content=msg["content"],
            )
        return episodic

    def recall_facts(self, query: str, top_k: int = 5) -> list[dict]:
        """Brain calls this to get relevant background knowledge."""
        return self._semantic.recall(query=query, top_k=top_k)

    def store(self, session_id: str, role: str, content: str) -> None:
        """
        Persist a new message.
        Always goes to both WorkingMemory and EpisodicMemory.
        """
        self._working.store(session_id=session_id, role=role, content=content)
        self._episodic.store(session_id=session_id, role=role, content=content)

    def forget(self, session_id: str) -> None:
        """Wipe session from working and episodic memory."""
        self._working.forget(session_id)
        self._episodic.forget(session_id)
        logger.info("[MemoryManager] Session forgotten | session=%s", session_id)

    # ------------------------------------------------------------------
    # Extended API (not in MemoryInterface — Brain can use directly)
    # ------------------------------------------------------------------

    def recall_similar(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Semantic search over past episodes.
        Returns episodes most similar to query across all sessions.
        """
        return self._episodic.recall_similar(query=query, top_k=top_k)

    def tag_last_outcome(self, session_id: str, outcome: str) -> None:
        """
        Tag the most recent episode in a session with an outcome.
        outcome: 'success' | 'failure' | 'partial'
        Brain calls this after observing the result of an action.
        """
        self._episodic.tag_last_outcome(session_id=session_id, outcome=outcome)

    def learn_fact(self, text: str, metadata: dict | None = None) -> str:
        """Store a new long-term fact. Returns fact ID."""
        return self._semantic.store_fact(text=text, metadata=metadata)

    def forget_fact(self, fact_id: str) -> None:
        """Remove a fact from semantic memory."""
        self._semantic.forget_fact(fact_id)

    # S3.3 RAG — document ingestion helpers

    def ingest_document(
        self,
        path: str,
        *,
        namespace: str | None = None,
        chunk_size: int = 150,
        chunk_overlap: int = 20,
        force: bool = False,
    ) -> int:
        """
        Load a file and store it as searchable chunks in semantic memory.
        Returns number of chunks stored (0 if already ingested and force=False).
        """
        return self._semantic.ingest_document(
            path,
            namespace=namespace,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            force=force,
        )

    def ingest_text(
        self,
        text: str,
        source: str,
        *,
        namespace: str | None = None,
        chunk_size: int = 150,
        chunk_overlap: int = 20,
        force: bool = False,
    ) -> int:
        """Chunk raw text and store in semantic memory. Returns chunk count."""
        return self._semantic.ingest_text(
            text,
            source=source,
            namespace=namespace,
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            force=force,
        )

    def list_sources(self) -> list[dict]:
        """List all ingested document sources with chunk counts."""
        return self._semantic.list_sources()

    def forget_source(self, source: str) -> int:
        """Delete all chunks from a document source. Returns deleted count."""
        return self._semantic.forget_source(source)

    def forget_namespace(self, namespace: str) -> int:
        """Delete all facts/chunks in a namespace. Returns deleted count."""
        return self._semantic.forget_namespace(namespace)

    # ------------------------------------------------------------------
    # S3.4 Procedural Memory — Skill Library + Trajectory Store
    # ------------------------------------------------------------------

    def register_skill(
        self,
        name: str,
        description: str,
        *,
        template: str | list | None = None,
        tags: list[str] | None = None,
    ) -> int:
        """Register (or update) a reusable skill. Returns skill id."""
        return self._procedural.register_skill(
            name, description, template=template, tags=tags,
        )

    def get_skill(self, name: str) -> dict | None:
        """Return skill record by name, or None."""
        return self._procedural.get_skill(name)

    def list_skills(self, tag: str | None = None) -> list[dict]:
        """List all skills, optionally filtered by tag."""
        return self._procedural.list_skills(tag=tag)

    def match_skill(self, query: str, top_k: int = 3) -> list[dict]:
        """
        Find skills semantically similar to the query.
        Brain calls this before planning to check if there's a known pattern.
        """
        return self._procedural.match_skill(query, top_k=top_k)

    def update_skill_stats(self, name: str, *, success: bool) -> None:
        """Track skill usage outcome."""
        self._procedural.update_skill_stats(name, success=success)

    def delete_skill(self, name: str) -> None:
        """Remove a skill and all its trajectories."""
        self._procedural.delete_skill(name)

    def save_trajectory(
        self,
        skill_name: str,
        input_summary: str,
        steps: list[dict],
        outcome: str,
        *,
        session_id: str | None = None,
        score: float = 1.0,
    ) -> int:
        """
        Record a complete task trajectory (input→steps→outcome).
        Used by S7.2 Skill Distiller to build fine-tuning datasets.
        Returns trajectory id.
        """
        return self._procedural.save_trajectory(
            skill_name, input_summary, steps, outcome,
            session_id=session_id, score=score,
        )

    def get_trajectories(
        self,
        skill_name: str | None = None,
        outcome: str | None = None,
        limit: int = 20,
    ) -> list[dict]:
        """Query trajectories by skill and/or outcome."""
        return self._procedural.get_trajectories(
            skill_name=skill_name, outcome=outcome, limit=limit,
        )

    def build_few_shots(self, skill_name: str, limit: int = 3) -> list[dict]:
        """
        Return top-scored successful trajectories as few-shot examples.
        Brain injects these into the LLM context for in-context learning.
        """
        return self._procedural.build_few_shots(skill_name, limit=limit)

    def analyze_failures(self, skill_name: str) -> dict:
        """Summarise failure patterns for a skill. Used by S7.2."""
        return self._procedural.analyze_failures(skill_name)

    def summarize_session(self, session_id: str, summary: str) -> None:
        """Brain can compress a session into a summary for long-term storage."""
        self._episodic.store_summary(session_id=session_id, summary=summary)
        self._working.forget(session_id)  # clear working after summarizing
        logger.info("[MemoryManager] Session summarized | session=%s", session_id)

    def prune_old_episodes(self, days: int = 30) -> int:
        """Delete episodes older than N days. For scheduled maintenance."""
        return self._episodic.forget_older_than(days=days)

    def status(self) -> dict:
        return {
            "working_sessions": self._working.active_sessions(),
            "semantic_facts": self._semantic.count(),
        }
