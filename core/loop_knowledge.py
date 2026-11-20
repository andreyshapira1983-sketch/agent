"""Writing knowledge gathered by the pipeline into long-term memory."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.knowledge_pipeline import RememberFn
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord


class AgentLoopKnowledge:
    """Mixed into ``AgentLoop``; state lives on the composed loop."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
        persistent_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Provided by a sibling mixin via the MRO; the cross-module link is recorded here.
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
