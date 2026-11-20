"""Facade for the memory-hygiene commands on the agent object."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

# Dotted import on purpose: the invariant guard (`core/loop_repair.py`) does not see `from core import X`,
# so a module imported only that way counts as orphaned.
import core.memory_hygiene_commands as hygiene


class AgentLoopHygiene:
    """Mixin for ``AgentLoop``; state lives on the composed loop."""

    if TYPE_CHECKING:  # pragma: no cover — только объявления
        log: Any
        persistent_store: Any
        episodic_store: Any
        assumption_store: Any

        def _durable_learning_suppressed(self, sink: str) -> bool: ...

        # Taken from a neighbouring mixin via the MRO; the cross-module link must be written down or it only shows at run time.
        model_router: Any

        def _file_read_workspace_root(self) -> Any: ...

    def _hygiene_suppressed_reason(self) -> str | None:
        """Why writing is not allowed, or `None` if it is."""
        if not self._durable_learning_suppressed("hygiene"):
            return None
        if getattr(self, "audit_read_only", False):
            return "audit_read_only"
        if getattr(self, "suppress_durable_learning_writes", False):
            return "dry_run_brake"
        return "not_allowlisted"

    def _hygiene_apply_brake(self) -> str | None:
        """Audit or dry run: manual cleanup only counts."""
        reason = self._hygiene_suppressed_reason()
        return reason if reason in {"audit_read_only", "dry_run_brake"} else None

    def run_maintenance_pass(self, *, dry_run: bool = True) -> dict:
        """Facade: the pass lives in `core/memory_hygiene_commands`."""
        return hygiene.run_maintenance_pass(
            log=self.log,
            persistent_store=self.persistent_store,
            episodic_store=self.episodic_store,
            assumption_store=getattr(self, "assumption_store", None),
            suppressed_reason=self._hygiene_suppressed_reason(),
            dry_run=dry_run,
            # The pass also sweeps migration .bak litter; the shadow only counts.
            workspace=self._file_read_workspace_root(),
            # Cleanup knows what procedure credit stands on.
            procedural_store=getattr(self, "procedural_store", None),
        )

    def compact_assumptions(self, *, dry_run: bool = False) -> dict:
        return hygiene.compact_assumptions(
            log=self.log,
            assumption_store=getattr(self, "assumption_store", None),
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
        )

    def expire_persistent(self, *, dry_run: bool = False):
        return hygiene.expire_persistent(
            log=self.log, persistent_store=self.persistent_store,
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
        )

    def dedupe_persistent(
        self, *, threshold: float | None = None, dry_run: bool = False,
    ):
        return hygiene.dedupe_persistent(
            log=self.log,
            persistent_store=self.persistent_store,
            threshold=threshold,
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
        )

    def dedupe_episodic(self, *, dry_run: bool = False) -> list[str]:
        return hygiene.dedupe_episodic(
            log=self.log, episodic_store=self.episodic_store,
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
            procedural_store=getattr(self, "procedural_store", None),
        )

    def prune_episodic(
        self,
        *,
        max_age_days: int = 30,
        min_quality: float = 0.4,
        staleness_threshold: float = 1.5,
        dry_run: bool = False,
    ) -> list[str]:
        # Anchors of procedure credit are never pruned, on the CLI path too.
        procs = getattr(self, "procedural_store", None)
        keep = (hygiene.cited_episode_ids(procs, self.episodic_store)
                if procs is not None and self.episodic_store is not None else frozenset())
        return hygiene.prune_episodic(
            log=self.log,
            episodic_store=self.episodic_store,
            max_age_days=max_age_days,
            min_quality=min_quality,
            staleness_threshold=staleness_threshold,
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
            keep_ids=keep,
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
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
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
            dry_run=dry_run or bool(self._hygiene_apply_brake()),
        )
