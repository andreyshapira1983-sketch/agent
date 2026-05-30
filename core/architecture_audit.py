"""Static architecture gap audit for the autonomous agent project.

This is an operator control-plane helper, not an LLM judgement. It checks
whether key architecture layers have concrete code/tests and highlights the
gaps that matter before moving from dry-run team planning to real sub-agent
execution.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ArchitectureLayerCheck:
    id: str
    title: str
    status: str
    evidence_files: tuple[str, ...]
    test_files: tuple[str, ...]
    summary: str
    next_step: str
    blocks_multi_agent_execution: bool = False

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "title": self.title,
            "status": self.status,
            "evidence_files": list(self.evidence_files),
            "test_files": list(self.test_files),
            "summary": self.summary,
            "next_step": self.next_step,
            "blocks_multi_agent_execution": self.blocks_multi_agent_execution,
        }


@dataclass(frozen=True)
class ArchitectureAudit:
    status_counts: dict[str, int]
    multi_agent_state: str
    ready_for_multi_agent_execution: bool
    checks: tuple[ArchitectureLayerCheck, ...]
    priority_gaps: tuple[ArchitectureLayerCheck, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "status_counts": self.status_counts,
            "multi_agent_state": self.multi_agent_state,
            "ready_for_multi_agent_execution": self.ready_for_multi_agent_execution,
            "priority_gaps": [gap.to_dict() for gap in self.priority_gaps],
            "checks": [check.to_dict() for check in self.checks],
        }

    def user_summary(self, *, limit: int = 8) -> str:
        lines = [
            "=== architecture audit ===",
            f"multi_agent_state: {self.multi_agent_state}",
            f"ready_for_multi_agent_execution: {self.ready_for_multi_agent_execution}",
            f"status_counts: {self.status_counts}",
        ]
        if self.priority_gaps:
            lines.append("priority gaps:")
            for gap in self.priority_gaps[:limit]:
                lines.append(
                    f"  - [{gap.status}] {gap.title}: {gap.summary}"
                )
                lines.append(f"    next: {gap.next_step}")
        else:
            lines.append("priority gaps: none")
        lines.append("layer checks:")
        for check in self.checks[:limit]:
            lines.append(
                f"  - [{check.status}] {check.title}"
                + (" (blocks multi-agent execution)" if check.blocks_multi_agent_execution else "")
            )
        return "\n".join(lines)


def audit_architecture(workspace: Path) -> ArchitectureAudit:
    checks = tuple(_build_checks(workspace))
    counts: dict[str, int] = {}
    for check in checks:
        counts[check.status] = counts.get(check.status, 0) + 1
    priority_gaps = tuple(
        check for check in checks
        if check.status == "gap" or check.blocks_multi_agent_execution
    )
    ready = not any(check.blocks_multi_agent_execution for check in checks)
    by_id = {check.id: check for check in checks}
    return ArchitectureAudit(
        status_counts=counts,
        multi_agent_state=_multi_agent_state(by_id),
        ready_for_multi_agent_execution=ready,
        checks=checks,
        priority_gaps=priority_gaps,
    )


def _multi_agent_state(checks: dict[str, ArchitectureLayerCheck]) -> str:
    if checks.get("team_executor_dry_run") and checks["team_executor_dry_run"].status == "present":
        return "dry_run_executor_ready"
    if checks.get("team_plan_contracts") and checks["team_plan_contracts"].status == "present":
        return "dry_run_contracts_only"
    return "not_started"


def _build_checks(workspace: Path) -> list[ArchitectureLayerCheck]:
    return [
        _check(
            workspace,
            id="doctrine_and_architecture_docs",
            title="Doctrine and Architecture Source of Truth",
            evidence=("AGENT_DOCTRINE.md", "архитектура автономного Агента.txt", "README.md"),
            tests=(),
            summary="Project intent and high-level architecture are documented.",
            next_step="Keep docs updated when execution semantics change.",
        ),
        _check(
            workspace,
            id="role_knowledge_learning",
            title="Role Router / Knowledge Use / Learning Planner",
            evidence=("core/role_router.py", "core/knowledge_use_policy.py", "core/learning_planner.py"),
            tests=("tests/test_role_router.py", "tests/test_knowledge_use_policy.py", "tests/test_learning_planner.py"),
            summary="Conversation role, knowledge scope and learning source selection exist.",
            next_step="Connect these decisions into long work-session reports.",
        ),
        _check(
            workspace,
            id="evidence_truth_pipeline",
            title="Evidence / Source Ranker / Conflict Resolver / Output Policy",
            evidence=("core/evidence.py", "core/source_ranker.py", "core/conflict_review.py", "core/output_policy.py"),
            tests=("tests/test_evidence.py", "tests/test_source_ranker.py", "tests/test_conflict_review.py", "tests/test_output_policy.py"),
            summary="Truth pipeline exists and can downgrade weak or conflicting evidence.",
            next_step="Add more realtime/live-source connectors when needed.",
        ),
        _check(
            workspace,
            id="model_router_budget",
            title="Model Router / Registry / Usage Budget",
            evidence=("core/model_router.py", "core/model_usage.py", "config/model_registry.example.json"),
            tests=("tests/test_model_router.py", "tests/test_model_usage.py"),
            summary="Models are routed by role and model calls are recorded with budget caps.",
            next_step="Keep model catalog updates explicit through config/model_registry.json.",
        ),
        _check(
            workspace,
            id="model_registry_audit",
            title="Model Registry Audit",
            evidence=("core/model_registry_audit.py",),
            tests=("tests/test_model_registry_audit.py",),
            summary="Operator can inspect active routes versus available registry candidates.",
            next_step="Later add provider-specific live catalog connectors if needed.",
        ),
        _check(
            workspace,
            id="release_hygiene_guard",
            title="Release Hygiene Guard",
            evidence=("core/release_hygiene.py",),
            tests=("tests/test_release_hygiene.py",),
            summary="Release manifest excludes local secrets and developer artifacts.",
            next_step="Add CI/pre-commit enforcement so unsafe artifacts fail before sharing.",
        ),
        _check(
            workspace,
            id="runtime_budget_approval",
            title="Autonomous Runtime / Budget / Circuit Breaker / Approval Inbox",
            evidence=("core/autonomous_runtime.py", "core/budget_governor.py", "core/circuit_breaker.py", "core/approval_inbox.py"),
            tests=("tests/test_autonomous_runtime.py", "tests/test_budget_governor.py", "tests/test_circuit_breaker.py", "tests/test_approval_inbox.py"),
            summary="Safe dry-run runtime, budgets, circuit breaker and persistent approvals exist.",
            next_step="Add a long work-session mode on top of runtime.",
        ),
        _check(
            workspace,
            id="self_repair_governance",
            title="Self-Repair / Governance / Rollback",
            evidence=("core/self_repair.py", "core/governance.py", "core/compensation.py"),
            tests=("tests/test_self_repair_e2e.py", "tests/test_governance.py", "tests/test_compensation.py"),
            summary="Repair proposals and governed apply/test/rollback paths exist.",
            next_step="Connect repair jobs into bounded autonomous work sessions.",
        ),
        _check(
            workspace,
            id="team_plan_contracts",
            title="Team Plan / Subagent Contracts",
            evidence=("core/team_plan.py",),
            tests=("tests/test_team_plan.py",),
            summary="Dry-run subagent contracts exist with tools, budgets and verifier fields.",
            next_step="Build a dry-run team executor before real delegation.",
        ),
        _check(
            workspace,
            id="team_executor_dry_run",
            title="Team Executor Dry-Run",
            evidence=("core/team_executor.py",),
            tests=("tests/test_team_executor.py",),
            summary="Dry-run executor walks subagent contracts without effects.",
            next_step="Connect executor reports into long work-session mode before real delegation.",
            blocks=True,
        ),
        _check(
            workspace,
            id="work_session_mode",
            title="Long Work Session Mode",
            evidence=("core/work_session.py",),
            tests=("tests/test_work_session.py",),
            summary="Missing operator command for bounded multi-hour autonomous work sessions.",
            next_step="Add work-session loop with time budget, periodic reports and circuit stop.",
            blocks=True,
        ),
        _check(
            workspace,
            id="persistent_budget_windows",
            title="Persistent Budget Windows",
            evidence=("core/budget_ledger.py",),
            tests=("tests/test_budget_ledger.py",),
            summary="Local JSONL budget ledger models hour/day spend windows across sessions.",
            next_step="Connect more runtime/tool counters into the persistent ledger as long sessions expand.",
            blocks=True,
        ),
        _check(
            workspace,
            id="subagent_memory_scope",
            title="Subagent Memory Scope",
            evidence=("core/subagent_memory_scope.py",),
            tests=("tests/test_subagent_memory_scope.py",),
            summary="Missing contract for what each subagent may read/write in memory.",
            next_step="Define memory scopes before real subagent execution.",
            blocks=True,
        ),
    ]


def _check(
    workspace: Path,
    *,
    id: str,
    title: str,
    evidence: tuple[str, ...],
    tests: tuple[str, ...],
    summary: str,
    next_step: str,
    blocks: bool = False,
) -> ArchitectureLayerCheck:
    evidence_present = all((workspace / path).exists() for path in evidence)
    tests_present = all((workspace / path).exists() for path in tests)
    if evidence_present and tests_present:
        status = "present"
    elif evidence_present:
        status = "started"
    else:
        status = "gap"
    return ArchitectureLayerCheck(
        id=id,
        title=title,
        status=status,
        evidence_files=evidence,
        test_files=tests,
        summary=summary,
        next_step=next_step,
        blocks_multi_agent_execution=blocks and status != "present",
    )
