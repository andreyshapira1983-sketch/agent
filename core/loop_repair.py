"""Facade of the operator repair commands on the agent object."""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

# Dotted form on purpose: `scripts/architecture_invariants.py` sees only `core` in `from core import ...`; the alias differs from the last segment to satisfy ruff PLC0414.
import core.repair_commands as repair_ops


class AgentLoopRepair:
    """Mixed into ``AgentLoop``; state lives on the composed loop."""

    if TYPE_CHECKING:  # pragma: no cover — declarations only
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
        """Facade: decisions live in `core/repair_commands.propose_repair`."""
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
        """Run one self-repair proposal through the MVP-13.2 controller."""
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
        """Facade: empty-case handling lives in `core/repair_commands.rollback`."""
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
        """Facade: defaults and journal writing live in `core/repair_commands`."""
        return repair_ops.cleanup_backups(
            workspace_root,
            log=self.log,
            keep_last=keep_last,
            max_age_days=max_age_days,
            dry_run=dry_run,
        )
