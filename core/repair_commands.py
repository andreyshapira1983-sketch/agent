"""Operator repair commands: propose, roll back, clean up backups.

Census item B1. This logic lived in `core/loop_repair.py`, a mixin composed into
`AgentLoop` — and the cycle never called it. Verified by call sites, not by
docstrings: `cli/commands_repair.py`, `cli/commands_memory.py`,
`cli/command_dispatch.py` and `core/self_repair.py` call it, and no phase of
`_run_inner` does. It sat in the loop layer because the CLI reached it through
the agent object.

The operator's ruling shapes what moved and what did not: `agent` stays the
single entry point for operator commands, so `agent.propose_repair(...)` still
works and no CLI call site changes — but the mixin stops being the home of the
logic. It keeps thin methods that pass their dependencies in; the decisions live
here.

Dependencies arrive as ARGUMENTS, never as an agent object. A function taking
`agent` and reaching into it with `getattr` would move the coupling rather than
remove it, and that duck-typed seam is a defect this census already recorded
elsewhere (`core/ingestion.py` finding `_remember_from_knowledge` by name).
"""
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

    Routed through ``for_task``, not ``for_role``: rewriting a whole module
    correctly is the hardest thing here, and ``for_role`` cannot escalate no
    matter how large the target is. The tier is computed from the job (file
    size, red tests) rather than from the request's wording, then passed as
    ``force_tier`` — which the router still puts through the operator gate, so
    this asks for the deep model and never grants it. With no
    ``AGENT_DEEP_MAX_CALLS_PER_SESSION`` budget set, the ask is refused and the
    behaviour is identical to before.
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
        # Unreadable target is the generator's error to report, not ours;
        # size 0 simply means "no case for the expensive model".
        _target_chars = 0

    def _select_llm(failing_tests: int):
        """Pick the model once the baseline is known.

        Deferred on purpose: the failing-test count is half the difficulty
        signal and only exists after `generate()` runs the baseline. An earlier
        version passed a literal 0 here, which silently disabled that half —
        the logic and its tests existed while production always saw zero.
        """
        tier = repair_complexity(
            target_chars=_target_chars, failing_tests=failing_tests
        )
        limit = deep_call_budget()
        escalation = OperatorEscalation(
            reason="high_value_repair",
            expected_output="minimal_patch_plan",
            budget_ok=deep_budget_ok(model_router.usage_ledger, limit=limit),
            # Never true here. A human typing `--reason` is approving; an
            # autonomous repair is not, and marking it approved would skip the
            # budget check that makes this safe.
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
        # Built from size alone, and only used if the generator never gets as
        # far as the baseline (e.g. an unreadable target).
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
    """Apply the most recent compensation plan (or one by id).

    Returns the `CompensationReport`. Three ways there is nothing to undo, and
    each says which: no workspace supplied, no plans registered, or a `plan_id`
    that matches none. A bare empty report would leave an operator unable to
    tell "nothing to roll back" from "your id was wrong".

    `workspace_root` is required because `AgentLoop` is workspace-agnostic by
    construction. The CLI passes it from the main() --workspace argument; tests
    pass the same root the producing tool used.

    Mutates `compensation_log` in place — the caller owns that list, and the
    popped plan must not come back on a second call.
    """
    from core.compensation import CompensationReport, apply_compensation_plan

    if workspace_root is None:
        report = CompensationReport(plan_id=plan_id or "", workspace_root="")
        log.log(
            "compensation_apply",
            {**report.summary(), "skipped_reason": "no workspace_root supplied"},
        )
        return report

    if not compensation_log:
        report = CompensationReport(
            plan_id=plan_id or "", workspace_root=str(Path(workspace_root).resolve())
        )
        log.log(
            "compensation_apply",
            {**report.summary(), "skipped_reason": "no plans registered"},
        )
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
                plan_id=plan_id, workspace_root=str(Path(workspace_root).resolve())
            )
            log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": f"plan_id '{plan_id}' not found"},
            )
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
