"""Фасад команд гигиены памяти на объекте агента."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Точечная форма намеренно — см. `core/loop_repair.py`: сторож инвариантов
# не видит `from core import X`, и модуль, импортированный только так,
# числится осиротевшим.
import core.memory_hygiene_commands as hygiene


class AgentLoopHygiene:
    """Подмешивается в ``AgentLoop``; состояние живёт на композированном цикле."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        assumption_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Берётся у соседней примеси: работает через MRO, но связь между
        # модулями обязана быть записана, иначе её видно только на прогоне.
        model_router: Any

        def _file_read_workspace_root(self) -> Any: ...

    def _hygiene_suppressed_reason(self) -> str | None:
        """Почему писать нельзя, или `None` если можно."""
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
            # MIR-125: проход метёт и .bak-мусор миграций; тень только считает.
            workspace=self._file_read_workspace_root(),
            # MIR-128: уборка знает, на чём стоит зачёт процедур.
            procedural_store=getattr(self, "procedural_store", None),
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

    def dedupe_episodic(self, *, dry_run: bool = False) -> list[str]:
        return hygiene.dedupe_episodic(
            log=self.log, episodic_store=self.episodic_store, dry_run=dry_run,
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
