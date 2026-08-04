"""Запись знаний, добытых конвейером, в долгую память.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

Конвейер знаний каталогизирует источники хода; отсюда в память уезжает то,
что он счёл достойным запоминания. Право на запись спрашивается тем же
`_durable_learning_suppressed`, что и у остальных долговременных приёмников:
конвейер не исключение из политики.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

from core.knowledge_pipeline import RememberFn
from core.memory_policy import MemoryWriteDecision
from core.models import MemoryRecord


class AgentLoopKnowledge:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется.
    """

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
        """Return a ``remember`` callback that loads the persistent store ONCE.

        The knowledge pipeline attempts a write for every extracted claim. On a
        repeated read-only turn (e.g. a work-session re-running the same goal)
        the same ~60 claims are re-extracted from the same files each cycle, and
        each `remember()` used to reload the entire store to run the dedup gate —
        an O(claims × records) disk reload every cycle whose writes are ~all
        rejected as duplicates (TD-019). This closure loads the snapshot a single
        time per pipeline pass and reuses it; ``remember`` keeps it current by
        appending any saved record, so dedup/echo behavior is unchanged.
        """
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
