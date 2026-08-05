"""Фасад операторских команд починки на объекте агента.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

**И это не код цикла — проверено по вызывающим, а не по этой строке.** Зовут
`cli/commands_repair.py`, `cli/commands_memory.py`, `cli/command_dispatch.py` и
`core/self_repair.py`. Ни одна фаза `_run_inner` сюда не заходит.

Что осталось здесь и почему (пункт B1 переписи, решение оператора). `agent`
остаётся ЕДИНОЙ операторской точкой входа: `agent.propose_repair(...)`
по-прежнему работает и ни один вызов в `cli/` не переписан. Но примесь больше
не дом настоящей логики — она передаёт зависимости и вызывает
`core/repair_commands.py`, где живут решения: выбор яруса модели с отложенным
выбором и разбор трёх пустых случаев отката.

Зависимости уезжают туда АРГУМЕНТАМИ. Функция, принимающая `agent` и лезущая в
него через `getattr`, перенесла бы связь, а не убрала — этот утиный шов
перепись уже нашла в другом месте.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

# Точечная форма намеренно. `from core import repair_commands` читается
# приятнее, но `scripts/architecture_invariants.py` собирает имена модулей и
# видит там только `core`, поэтому модуль, импортированный ТОЛЬКО так, числится
# осиротевшим. Псевдоним отличается от последнего сегмента, иначе ruff (PLC0414)
# справедливо просит `from … import …` обратно.
import core.repair_commands as repair_ops


class AgentLoopRepair:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле.

    Члены ниже — объявления контракта хоста (``AgentLoop`` их создаёт в
    ``__init__``); присваиваний нет, поэтому во время выполнения ничего не
    создаётся и не затеняется.
    """

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        model_router: Any
        compensation_log: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

    def propose_repair(
        self,
        *,
        target_path: str,
        workspace_root: Path,
        test_paths: tuple[str, ...] = ("tests",),
        test_pattern: str | None = None,
        trace_id: str | None = None,
        extra_context: str = "",
    ):
        """Фасад: решения живут в `core/repair_commands.propose_repair`."""
        return repair_ops.propose_repair(
            model_router=self.model_router,
            log=self.log,
            target_path=target_path,
            workspace_root=workspace_root,
            test_paths=test_paths,
            test_pattern=test_pattern,
            trace_id=trace_id,
            extra_context=extra_context,
        )

    def repair(
        self,
        proposal: Any,
        *,
        workspace_root: Path,
    ):
        """Run one self-repair proposal through the MVP-13.2 controller.

        Stayed a direct delegation: the controller takes the AGENT itself, so
        routing it through `repair_commands` would add a hop without moving any
        decision. There is no logic here to move.
        """
        from core.self_repair import SelfRepairController

        return SelfRepairController(
            self,
            workspace_root=workspace_root,
        ).run(proposal)

    def rollback(
        self,
        plan_id: str | None = None,
        *,
        workspace_root: Path | None = None,
    ):
        """Фасад: разбор пустых случаев живёт в `core/repair_commands.rollback`."""
        return repair_ops.rollback(
            compensation_log=self.compensation_log,
            log=self.log,
            plan_id=plan_id,
            workspace_root=workspace_root,
        )

    def cleanup_backups(
        self,
        workspace_root,
        *,
        keep_last: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Фасад: умолчания и запись в журнал — в `core/repair_commands`."""
        return repair_ops.cleanup_backups(
            workspace_root,
            log=self.log,
            keep_last=keep_last,
            max_age_days=max_age_days,
            dry_run=dry_run,
        )
