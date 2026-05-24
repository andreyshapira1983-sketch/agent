"""
brain/interfaces/memory_interface.py — Memory as a Tool

The Brain reads from and writes to memory.
Memory is a passive store — it does NOT influence the Brain directly.
The Brain decides what to remember and what to forget.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class MemoryInterface(ABC):
    """
    Abstract interface for any memory backend.
    (in-memory dict, Redis, vector DB, SQL — all interchangeable)
    """

    @abstractmethod
    def recall_history(self, session_id: str, limit: int = 10) -> list[dict]:
        """Return last N messages for this session."""
        raise NotImplementedError

    @abstractmethod
    def recall_facts(self, query: str, top_k: int = 5) -> list[dict]:
        """Semantic search over long-term fact store."""
        raise NotImplementedError

    def recall_similar(self, query: str, top_k: int = 5) -> list[dict]:
        """
        Semantic search over past episodes (conversations).
        Optional — implementations may leave as empty list.
        """
        return []

    @abstractmethod
    def store(self, session_id: str, role: str, content: str) -> None:
        """Persist a message to memory."""
        raise NotImplementedError

    @abstractmethod
    def forget(self, session_id: str) -> None:
        """Wipe session memory — Brain decides when to forget."""
        raise NotImplementedError
