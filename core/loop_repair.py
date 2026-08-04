"""Починка кода: предложение, применение, откат, уборка резервных копий.

`core/loop_methods.py`, откуда это приехало, не было модулем: его сделал
`core/incremental_splitter.py`, резавший `core/loop.py` по бюджету строк, а не
по смыслу. Имя `methods` — это «остальное», и по нему нельзя было узнать, что
внутри лежат пять несвязанных ответственностей.

Тоже не код цикла: зовут `cli/commands_repair.py`, `cli/commands_memory.py` и
`core/self_repair.py`.

Ключевое место — маршрут модели в `propose_repair`. Запрос обязан идти через
`for_task` с отложенным выбором (`llm_selector`), а не через `for_role`:
`for_role` не умеет эскалировать вообще, а выбор модели ДО прогона базовых
тестов означает, что число падающих тестов — половина сигнала сложности — не
может повлиять на решение. Это пинится в `tests/test_repair_routes_by_complexity.py`,
и тот тест читает исходник этого файла.
"""
from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any

from core.model_router import ModelRole


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
        """Generate a guarded RepairProposal without applying it.

        Routed through ``for_task``, not ``for_role``: rewriting a whole module
        correctly is the hardest thing here, and ``for_role`` cannot escalate no
        matter how large the target is. The tier is computed from the job (file
        size, red tests) rather than from the request's wording, then passed as
        ``force_tier`` — which the router still puts through the operator gate,
        so this asks for the deep model and never grants it. With no
        ``AGENT_DEEP_MAX_CALLS_PER_SESSION`` budget set, the ask is refused and
        the behaviour is identical to before.
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
            signal and only exists after `generate()` runs the baseline. An
            earlier version passed a literal 0 here, which silently disabled
            that half — the logic and its tests existed while production always
            saw zero.
            """
            tier = repair_complexity(
                target_chars=_target_chars, failing_tests=failing_tests
            )
            limit = deep_call_budget()
            escalation = OperatorEscalation(
                reason="high_value_repair",
                expected_output="minimal_patch_plan",
                budget_ok=deep_budget_ok(self.model_router.usage_ledger, limit=limit),
                # Never true here. A human typing `--reason` is approving; an
                # autonomous repair is not, and marking it approved would skip
                # the budget check that makes this safe.
                operator_approved=False,
            )
            return self.model_router.for_task(
                ModelRole.REPAIR_PROPOSAL,
                f"repair {target_path}",
                escalation=escalation,
                force_tier=tier,
            )

        return RepairProposalGenerator(
            workspace_root=workspace_root,
            # Built from size alone, and only used if the generator never gets
            # as far as the baseline (e.g. an unreadable target).
            llm=self.model_router.for_role(ModelRole.REPAIR_PROPOSAL),
            llm_selector=_select_llm,
            logger=self.log,
        ).generate(
            target_path=target_path,
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
        """Apply the most recent compensation plan (or one by id).

        Returns the `CompensationReport`. When the log is empty, the
        report carries zero outcomes and the audit event records the
        no-op. When `plan_id` is provided, the matching plan is
        removed from the log; otherwise the LAST plan is popped.

        `workspace_root` is required because `AgentLoop` is workspace-
        agnostic by construction. The CLI passes it from the main()
        --workspace argument; tests pass the same root the producing
        tool used.
        """
        from core.compensation import CompensationReport, apply_compensation_plan

        if workspace_root is None:
            report = CompensationReport(plan_id=plan_id or "", workspace_root="")
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no workspace_root supplied"},
            )
            return report

        if not self.compensation_log:
            report = CompensationReport(
                plan_id=plan_id or "", workspace_root=str(Path(workspace_root).resolve())
            )
            self.log.log(
                "compensation_apply",
                {**report.summary(), "skipped_reason": "no plans registered"},
            )
            return report

        if plan_id is None:
            plan = self.compensation_log.pop()
        else:
            for i, p in enumerate(self.compensation_log):
                if p.id == plan_id:
                    plan = self.compensation_log.pop(i)
                    break
            else:
                report = CompensationReport(
                    plan_id=plan_id, workspace_root=str(Path(workspace_root).resolve())
                )
                self.log.log(
                    "compensation_apply",
                    {**report.summary(), "skipped_reason": f"plan_id '{plan_id}' not found"},
                )
                return report

        report = apply_compensation_plan(plan, Path(workspace_root))
        self.log.log("compensation_apply", report.summary())
        return report

    def cleanup_backups(
        self,
        workspace_root,
        *,
        keep_last: int | None = None,
        max_age_days: int | None = None,
        dry_run: bool = False,
    ):
        """Delete old `.bak.<ts>` files under the workspace root."""
        from core.hygiene import (
            DEFAULT_KEEP_LAST,
            DEFAULT_MAX_AGE_DAYS,
            cleanup_backups,
        )

        report = cleanup_backups(
            workspace_root,
            keep_last=DEFAULT_KEEP_LAST if keep_last is None else keep_last,
            max_age_days=DEFAULT_MAX_AGE_DAYS if max_age_days is None else max_age_days,
            dry_run=dry_run,
        )
        self.log.log("backup_cleanup", report.summary())
        return report
