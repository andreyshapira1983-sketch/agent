"""Запись знаний, добытых конвейером, в долгую память."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.knowledge_pipeline import RememberFn
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord


class AgentLoopKnowledge:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        persistent_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        remember: Any

    def _remember_from_knowledge(
        self,
        content: str,
        tags: list[str],
        source: str,
        record_type: str,
        owner: str,
    ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
        return self.remember(
            content=content,
            tags=tags,
            source=source,
            record_type=record_type,
            owner=owner,
        )

    def _knowledge_remember_batch(self) -> RememberFn:
        """Return a ``remember`` callback that loads the persistent store ONCE."""
        snapshot: list[MemoryRecord] = (
            self.persistent_store.load() if self.persistent_store is not None else []
        )

        def _remember(
            content: str,
            tags: list[str],
            source: str,
            record_type: str,
            owner: str,
        ) -> tuple[MemoryWriteDecision, MemoryRecord | None]:
            return self.remember(
                content=content,
                tags=tags,
                source=source,
                record_type=record_type,
                owner=owner,
                existing=snapshot,
            )

        return _remember
