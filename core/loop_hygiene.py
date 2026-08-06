"""Фасад команд гигиены памяти на объекте агента.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

**И это не код цикла — проверено по вызывающим.** `run_maintenance_pass` зовёт
`agent_tick.py`, отдельные шаги — `cli/commands_memory.py`. Ни одна фаза
`_run_inner` сюда не заходит.

Что осталось здесь и почему (пункт B1 переписи, решение оператора). `agent`
остаётся ЕДИНОЙ операторской точкой входа: `agent.run_maintenance_pass()` и
`agent.expire_persistent(...)` работают как прежде, ни один вызывающий не
переписан. Реализация уехала в `core/memory_hygiene_commands.py`.

Шаги по-прежнему доступны поодиночке: проход целиком удобен автоматике, а
оператору бывает нужно ровно одно — просрочить, схлопнуть дубли, подрезать
эпизоды или заархивировать. Один вход на всё лишил бы его этой возможности.

**Право на запись НЕ уехало и уехать не может.** Кто вправе писать — вопрос
политики агента (`_durable_learning_suppressed`, тормоз аудита, тормоз сухого
прогона, список разрешённых стоков). Ответ на него вычисляется здесь и уезжает
вниз готовым вердиктом: вторая копия этого решения в другом месте — ровно тот
класс дефекта, который перепись искала.
"""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Точечная форма намеренно — см. `core/loop_repair.py`: сторож инвариантов
# не видит `from core import X`, и модуль, импортированный только так,
# числится осиротевшим.
import core.memory_hygiene_commands as hygiene


class AgentLoopHygiene:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        assumption_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        model_router: Any

    def _hygiene_suppressed_reason(self) -> str | None:
        """Почему писать нельзя, или `None` если можно.

        Ворота остаются на агенте: сток `hygiene` судится тем же правилом, что
        всякая долговременная запись, и это то, что делает `:audit` и тормоз
        сухого прогона абсолютными. Подсистеме уезжает готовый ответ, а не
        право его вычислять заново.
        """
        if not self._durable_learning_suppressed("hygiene"):
            return None
        if getattr(self, "audit_read_only", False):
            return "audit_read_only"
        if getattr(self, "suppress_durable_learning_writes", False):
            return "dry_run_brake"
        return "not_allowlisted"

    def run_maintenance_pass(self, *, dry_run: bool = True) -> dict:
        """Фасад: проход живёт в `core/memory_hygiene_commands`."""
        return hygiene.run_maintenance_pass(
            log=self.log,
            persistent_store=self.persistent_store,
            episodic_store=self.episodic_store,
            assumption_store=getattr(self, "assumption_store", None),
            suppressed_reason=self._hygiene_suppressed_reason(),
            dry_run=dry_run,
        )

    def compact_assumptions(self, *, dry_run: bool = False) -> dict:
        return hygiene.compact_assumptions(
            log=self.log,
            assumption_store=getattr(self, "assumption_store", None),
            dry_run=dry_run,
        )

    def expire_persistent(self, *, dry_run: bool = False):
        return hygiene.expire_persistent(
            log=self.log, persistent_store=self.persistent_store, dry_run=dry_run,
        )

    def dedupe_persistent(
        self, *, threshold: float | None = None, dry_run: bool = False,
    ):
        return hygiene.dedupe_persistent(
            log=self.log,
            persistent_store=self.persistent_store,
            threshold=threshold,
            dry_run=dry_run,
        )

    def prune_episodic(
        self,
        *,
        max_age_days: int = 30,
        min_quality: float = 0.4,
        staleness_threshold: float = 1.5,
        dry_run: bool = False,
    ) -> list[str]:
        return hygiene.prune_episodic(
            log=self.log,
            episodic_store=self.episodic_store,
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            dry_run=dry_run,
        )

    def summarise_persistent(
        self, tag: str, *, max_records: int | None = None, dry_run: bool = False,
    ):
        return hygiene.summarise_persistent(
            tag,
            log=self.log,
            persistent_store=self.persistent_store,
            model_router=self.model_router,
            max_records=max_records,
            dry_run=dry_run,
        )

    def archive_persistent(
        self,
        *,
        threshold: float | None = None,
        min_age_days: int | None = None,
        dry_run: bool = False,
    ):
        return hygiene.archive_persistent(
            log=self.log,
            persistent_store=self.persistent_store,
            threshold=threshold,
            min_age_days=min_age_days,
            dry_run=dry_run,
        )
