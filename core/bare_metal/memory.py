"""Memory Manager — трёхуровневая система памяти.

Уровни:
    Working  — краткосрочная (последние N записей, FIFO).
    Episodic — среднесрочная (прошлые взаимодействия, eviction по importance).
    Semantic — долгосрочная (факты / знания, eviction по importance).

Поиск: cosine similarity по embedding-ам из трансформера.
"""

from __future__ import annotations

import json
import os
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable

import torch

_COSINE_SIMILARITY: Callable[..., Any] = torch.nn.functional.cosine_similarity  # type: ignore[attr-defined]  # pylint: disable=not-callable,invalid-name


@dataclass
class MemoryEntry:
    text: str
    embedding: torch.Tensor
    timestamp: float = field(default_factory=time.time)
    importance: float = 0.5
    metadata: dict = field(default_factory=dict)


class MemoryManager:
    def __init__(
        self,
        embedding_dim: int,
        working_capacity: int = 32,
        episodic_capacity: int = 1000,
        semantic_capacity: int = 5000,
    ):
        self.dim = embedding_dim
        self.working: deque[MemoryEntry] = deque(maxlen=working_capacity)
        self.episodic: list[MemoryEntry] = []
        self.episodic_cap = episodic_capacity
        self.semantic: list[MemoryEntry] = []
        self.semantic_cap = semantic_capacity

    # ── Store ─────────────────────────────────────────────────────────────

    def store_working(self, text: str, embedding: torch.Tensor,
                      metadata: dict | None = None) -> None:
        self.working.append(MemoryEntry(
            text=text, embedding=embedding.detach().clone(),
            metadata=metadata or {},
        ))

    def store_episodic(self, text: str, embedding: torch.Tensor,
                       importance: float = 0.5) -> None:
        self.episodic.append(MemoryEntry(
            text=text, embedding=embedding.detach().clone(),
            importance=importance,
        ))
        if len(self.episodic) > self.episodic_cap:
            self.episodic.sort(key=lambda e: e.importance, reverse=True)
            self.episodic = self.episodic[:self.episodic_cap]

    def store_semantic(self, text: str, embedding: torch.Tensor,
                       importance: float = 0.7) -> None:
        self.semantic.append(MemoryEntry(
            text=text, embedding=embedding.detach().clone(),
            importance=importance,
        ))
        if len(self.semantic) > self.semantic_cap:
            self.semantic.sort(key=lambda e: e.importance, reverse=True)
            self.semantic = self.semantic[:self.semantic_cap]

    # ── Recall (cosine similarity) ────────────────────────────────────────

    def recall(self, query: torch.Tensor, k: int = 5,
               scope: str = 'all') -> list[MemoryEntry]:
        """Возвращает top-k записей по cosine similarity к query embedding."""
        candidates: list[MemoryEntry] = []
        if scope in ('all', 'working'):
            candidates.extend(self.working)
        if scope in ('all', 'episodic'):
            candidates.extend(self.episodic)
        if scope in ('all', 'semantic'):
            candidates.extend(self.semantic)

        if not candidates:
            return []

        # Stack embeddings → (N, dim), query → (1, dim)
        embs = torch.stack([e.embedding for e in candidates])
        q = query.detach().unsqueeze(0)
        sims = _COSINE_SIMILARITY(q, embs, dim=-1)  # pylint: disable=not-callable

        top_k = min(k, len(candidates))
        _, indices = sims.topk(top_k)
        return [candidates[i] for i in indices.tolist()]

    # ── Stats ─────────────────────────────────────────────────────────────

    def stats(self) -> dict:
        return {
            'working': len(self.working),
            'episodic': len(self.episodic),
            'semantic': len(self.semantic),
        }

    # ── Persistence ───────────────────────────────────────────────────────

    def save(self, path: str) -> None:
        """Сохраняет episodic + semantic память на диск."""
        os.makedirs(os.path.dirname(path) or '.', exist_ok=True)
        data = {
            'episodic': self._entries_to_serial(self.episodic),
            'semantic': self._entries_to_serial(self.semantic),
        }
        torch.save(data, path)

    def load(self, path: str) -> None:
        if not os.path.exists(path):
            return
        data = torch.load(path, map_location='cpu', weights_only=False)
        self.episodic = self._serial_to_entries(data.get('episodic', []))
        self.semantic = self._serial_to_entries(data.get('semantic', []))

    @staticmethod
    def _entries_to_serial(entries: list[MemoryEntry]) -> list[dict]:
        return [
            {
                'text': e.text,
                'embedding': e.embedding,
                'timestamp': e.timestamp,
                'importance': e.importance,
                'metadata': e.metadata,
            }
            for e in entries
        ]

    @staticmethod
    def _serial_to_entries(items: list[dict]) -> list[MemoryEntry]:
        return [
            MemoryEntry(
                text=d['text'],
                embedding=d['embedding'],
                timestamp=d.get('timestamp', 0.0),
                importance=d.get('importance', 0.5),
                metadata=d.get('metadata', {}),
            )
            for d in items
        ]
