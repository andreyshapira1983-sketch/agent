"""
brain/memory/working_memory.py — Working Memory (Short-Term)

Lives only while the session is active. Stored in RAM.
When the session ends — this memory is gone.

Analogy: what you're thinking about RIGHT NOW.
Capacity is intentionally limited — like human working memory.
"""

from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime
from typing import Deque

logger = logging.getLogger(__name__)

# Working memory holds only the last N messages per session
# Beyond this limit — oldest messages are dropped automatically
WORKING_MEMORY_LIMIT = 20


@dataclass
class Message:
    role: str        # "user" | "assistant" | "system" | "tool"
    content: str
    timestamp: datetime = field(default_factory=datetime.utcnow)

    def to_dict(self) -> dict:
        return {
            "role": self.role,
            "content": self.content,
            "timestamp": self.timestamp.isoformat(),
        }


class WorkingMemory:
    """
    Per-session short-term memory.
    Fast. In-RAM. Auto-evicts oldest messages when full.
    Brain reads this first — it's the most recent context.
    """

    def __init__(self, limit: int = WORKING_MEMORY_LIMIT) -> None:
        self._limit = limit
        # session_id → deque of Messages
        self._store: dict[str, Deque[Message]] = {}

    def store(self, session_id: str, role: str, content: str) -> None:
        if session_id not in self._store:
            self._store[session_id] = deque(maxlen=self._limit)
        self._store[session_id].append(Message(role=role, content=content))
        logger.debug("[WorkingMemory] Stored | session=%s role=%s", session_id, role)

    def recall(self, session_id: str, limit: int = 10) -> list[dict]:
        """Return last N messages for this session, newest last."""
        if session_id not in self._store:
            return []
        messages = list(self._store[session_id])
        return [m.to_dict() for m in messages[-limit:]]

    def forget(self, session_id: str) -> None:
        """Wipe entire session from working memory."""
        self._store.pop(session_id, None)
        logger.info("[WorkingMemory] Session cleared | session=%s", session_id)

    def active_sessions(self) -> list[str]:
        return list(self._store.keys())

    def size(self, session_id: str) -> int:
        return len(self._store.get(session_id, []))
