"""Operator repair commands (propose, roll back, clean up backups); dependencies arrive as ARGUMENTS, never as an agent object."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.model_router import ModelRole


def propose_repair(
    *,
    model_router: Any,
    log: Any,
    target_path: str,
    workspace_root: Path,
    test_paths: tuple[str, ...] = ("tests",),
    test_pattern: str | None = None,
    trace_id: str | None = None,
    extra_context: str = "",
):
    """Generate a guarded RepairProposal without applying it.

    Routed through ``for_task`` with the tier computed from the job (file size, red tests) and passed as ``force_tier``;
    the router still applies the operator gate, so this asks for the deep model and never grants it.
    """
    from core.deep_escalation import (
        OperatorEscalation,
        deep_budget_ok,
        deep_call_budget,
    )
    from core.repair_proposal import RepairProposalGenerator, repair_complexity

    try:
        _target_chars = len(
            (Path(workspace_root) / target_path).read_text(
                encoding="utf-8", errors="replace"
            )
        )
    except OSError:
        # An unreadable target is the generator's error to report (size 0 = no case for the expensive model);
        # silent here on purpose: the generator reports `file_read failed` with status `tool_error`.
        _target_chars = 0

    def _select_llm(failing_tests: int):
        """Pick the model once the baseline is known."""
        tier = repair_complexity(
            target_chars=_target_chars, failing_tests=failing_tests
        )
        limit = deep_call_budget()
        escalation = OperatorEscalation(
            reason="high_value_repair",
            expected_output="minimal_patch_plan",
            budget_ok=deep_budget_ok(model_router.usage_ledger, limit=limit),
            # Never true here: a human typing `--reason` is approving, an autonomous repair is not (approved would skip the budget check).
            operator_approved=False,
        )
        return model_router.for_task(
            ModelRole.REPAIR_PROPOSAL,
            f"repair {target_path}",
            escalation=escalation,
            force_tier=tier,
        )

    return RepairProposalGenerator(
        workspace_root=workspace_root,
        # Built from size alone; used only if the generator never reaches the baseline (e.g. unreadable target).
        llm=model_router.for_role(ModelRole.REPAIR_PROPOSAL),
        llm_selector=_select_llm,
        logger=log,
    ).generate(
        target_path=target_path,
        test_paths=test_paths,
        test_pattern=test_pattern,
        trace_id=trace_id,
        extra_context=extra_context,
    )


def rollback(
    *,
    compensation_log: list[Any],
    log: Any,
    plan_id: str | None = None,
    workspace_root: Path | None = None,
):
    """Apply the most recent compensation plan (or one by id), popping it from `compensation_log` in place (the caller owns that list)."""
    from core.compensation import CompensationReport, apply_compensation_plan

    if workspace_root is None:
        report = CompensationReport(
            plan_id=plan_id or "", workspace_root="", skipped_reason="no workspace_root supplied"
        )
        log.log("compensation_apply", report.summary())
        return report

    if not compensation_log:
        report = CompensationReport(
            plan_id=plan_id or "", workspace_root=str(Path(workspace_root).resolve()),
            skipped_reason="no plans registered",
        )
        log.log("compensation_apply", report.summary())
        return report

    if plan_id is None:
        plan = compensation_log.pop()
    else:
        for i, p in enumerate(compensation_log):
            if p.id == plan_id:
                plan = compensation_log.pop(i)
                break
        else:
            report = CompensationReport(
                plan_id=plan_id, workspace_root=str(Path(workspace_root).resolve()),
                skipped_reason=f"plan_id '{plan_id}' not found",
            )
            log.log("compensation_apply", report.summary())
            return report

    report = apply_compensation_plan(plan, Path(workspace_root))
    log.log("compensation_apply", report.summary())
    return report


def cleanup_backups(
    workspace_root,
    *,
    log: Any,
    keep_last: int | None = None,
    max_age_days: int | None = None,
    dry_run: bool = False,
):
    """Delete old `.bak.<ts>` files under the workspace root."""
    from core.backup_cleanup import (
        DEFAULT_KEEP_LAST,
        DEFAULT_MAX_AGE_DAYS,
    )
    from core.backup_cleanup import (
        cleanup_backups as _cleanup,
    )

    report = _cleanup(
        workspace_root,
        keep_last=DEFAULT_KEEP_LAST if keep_last is None else keep_last,
        max_age_days=DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days,
        dry_run=dry_run,
    )
    log.log("backup_cleanup", report.summary())
    return report
