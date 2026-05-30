"""Dry-run multi-agent team planning.

This is the first layer of the multi-agent organisation model. It does not run
subagents. It creates bounded contracts that answer: which role is useful, why,
what may it do, what must it return, what budget does it get, and who verifies
the result.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable


_KNOWN_TOOLS = {
    "file_read",
    "web_search",
    "web_fetch",
    "rss_fetch",
    "read_logs",
    "run_tests",
    "diff_file",
    "file_write",
    "shell_exec",
}


@dataclass(frozen=True)
class SubagentContract:
    name: str
    role: str
    objective: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    allowed_tools: tuple[str, ...] = ()
    forbidden_tools: tuple[str, ...] = ()
    model_role: str = "synthesizer"
    max_iterations: int = 1
    max_model_calls: int = 1
    max_cost_units: int = 3
    verifier: str = "VerifierAgent"
    stop_conditions: tuple[str, ...] = ("output_contract_satisfied", "budget_exhausted")
    risk_level: str = "low"
    approval_required: bool = False

    def __post_init__(self) -> None:
        if not self.name or not self.name.isascii():
            raise ValueError("subagent name must be non-empty ASCII")
        if self.max_iterations < 1:
            raise ValueError("max_iterations must be >= 1")
        if self.max_model_calls < 0 or self.max_cost_units < 0:
            raise ValueError("budget values must be non-negative")
        unknown_allowed = set(self.allowed_tools) - _KNOWN_TOOLS
        unknown_forbidden = set(self.forbidden_tools) - _KNOWN_TOOLS
        if unknown_allowed or unknown_forbidden:
            raise ValueError("subagent contract contains unknown tools")
        overlap = set(self.allowed_tools) & set(self.forbidden_tools)
        if overlap:
            raise ValueError("subagent tool cannot be both allowed and forbidden")

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "role": self.role,
            "objective": self.objective,
            "inputs": list(self.inputs),
            "outputs": list(self.outputs),
            "allowed_tools": list(self.allowed_tools),
            "forbidden_tools": list(self.forbidden_tools),
            "model_role": self.model_role,
            "max_iterations": self.max_iterations,
            "max_model_calls": self.max_model_calls,
            "max_cost_units": self.max_cost_units,
            "verifier": self.verifier,
            "stop_conditions": list(self.stop_conditions),
            "risk_level": self.risk_level,
            "approval_required": self.approval_required,
        }


@dataclass(frozen=True)
class TeamPlan:
    goal: str
    needed: bool
    reasoning: str
    contracts: tuple[SubagentContract, ...] = ()
    total_model_calls: int = 0
    total_cost_units: int = 0
    warnings: tuple[str, ...] = ()
    stop_conditions: tuple[str, ...] = (
        "all_contracts_completed",
        "budget_exhausted",
        "human_abort",
    )

    def to_dict(self) -> dict[str, Any]:
        return {
            "goal": self.goal,
            "needed": self.needed,
            "reasoning": self.reasoning,
            "contracts": [contract.to_dict() for contract in self.contracts],
            "total_model_calls": self.total_model_calls,
            "total_cost_units": self.total_cost_units,
            "warnings": list(self.warnings),
            "stop_conditions": list(self.stop_conditions),
        }

    def user_summary(self) -> str:
        if not self.needed:
            return f"(team-plan: not needed) {self.reasoning}"
        lines = [
            "=== team plan ===",
            f"goal: {self.goal}",
            f"reasoning: {self.reasoning}",
            f"budget: calls={self.total_model_calls} cost_units={self.total_cost_units}",
        ]
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        lines.append("contracts:")
        for contract in self.contracts:
            tools = ",".join(contract.allowed_tools) or "-"
            lines.append(
                f"  - {contract.name}: role={contract.role} "
                f"tools={tools} calls={contract.max_model_calls} "
                f"cost={contract.max_cost_units} approval={contract.approval_required}"
            )
            lines.append(f"    objective: {contract.objective}")
            lines.append(f"    outputs: {', '.join(contract.outputs)}")
        return "\n".join(lines)


class TeamPlanner:
    """Rule-based first pass for deciding whether a subagent team is useful."""

    def plan(self, goal: str, *, limit: int = 5) -> TeamPlan:
        goal = goal.strip()
        if not goal:
            raise ValueError("team plan goal must be non-empty")
        if limit < 1:
            raise ValueError("limit must be >= 1")

        signals = _goal_signals(goal)
        if not _needs_team(goal, signals):
            return TeamPlan(
                goal=goal,
                needed=False,
                reasoning=(
                    "The task looks narrow enough for one agent; creating "
                    "subagents would add coordination cost."
                ),
            )

        contracts = _contracts_for_signals(goal, signals)
        contracts = tuple(_dedupe_contracts(contracts))[:limit]
        warnings = []
        if limit < len(tuple(_contracts_for_signals(goal, signals))):
            warnings.append("team plan was truncated by --limit")
        if any(c.approval_required for c in contracts):
            warnings.append("some contracts require human approval before effects")
        return TeamPlan(
            goal=goal,
            needed=True,
            reasoning=(
                "The task spans multiple concerns; bounded subagent contracts "
                "reduce chaos and make budget/verification explicit."
            ),
            contracts=contracts,
            total_model_calls=sum(c.max_model_calls for c in contracts),
            total_cost_units=sum(c.max_cost_units for c in contracts),
            warnings=tuple(warnings),
        )


def _goal_signals(goal: str) -> set[str]:
    lower = goal.lower()
    signals: set[str] = set()
    if any(word in lower for word in ("news", "новост", "trend", "signal", "рынок", "market")):
        signals.add("news")
    if any(word in lower for word in ("business", "client", "клиент", "заработ", "sell", "sales", "ниша")):
        signals.add("business")
    if any(word in lower for word in ("code", "код", "github", "test", "bug", "patch", "repair")):
        signals.add("code")
    if any(word in lower for word in ("architecture", "архитект", "agent", "агент", "multi", "подагент")):
        signals.add("architecture")
    if any(word in lower for word in ("budget", "cost", "token", "бюджет", "стоим", "ключ")):
        signals.add("budget")
    if any(word in lower for word in ("verify", "source", "evidence", "провер", "источник")):
        signals.add("verify")
    return signals


def _needs_team(goal: str, signals: set[str]) -> bool:
    if len(signals) >= 2:
        return True
    words = [word for word in goal.replace("\n", " ").split(" ") if word.strip()]
    return len(words) >= 18 and bool(signals)


def _contracts_for_signals(goal: str, signals: set[str]) -> tuple[SubagentContract, ...]:
    contracts: list[SubagentContract] = []
    contracts.append(_budget_watch_agent(goal))
    if "news" in signals:
        contracts.append(_news_signal_agent(goal))
    if "business" in signals:
        contracts.append(_business_opportunity_agent(goal))
    if "code" in signals:
        contracts.append(_coder_signal_agent(goal))
    if "architecture" in signals:
        contracts.append(_architecture_impact_agent(goal))
    if "verify" in signals or len(signals) > 1:
        contracts.append(_verifier_agent(goal))
    return tuple(contracts)


def _dedupe_contracts(contracts: Iterable[SubagentContract]) -> list[SubagentContract]:
    seen: set[str] = set()
    result: list[SubagentContract] = []
    for contract in contracts:
        if contract.name in seen:
            continue
        seen.add(contract.name)
        result.append(contract)
    return result


def _budget_watch_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="BudgetWatchAgent",
        role="budget_guard",
        objective=f"Estimate and cap the model/tool spend for: {goal}",
        inputs=("team_goal", "model_policy", "usage_limits"),
        outputs=("budget_summary", "stop_recommendation"),
        model_role="verifier",
        max_model_calls=1,
        max_cost_units=1,
    )


def _news_signal_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="NewsSignalAgent",
        role="read_only_signal_collector",
        objective=f"Find high-signal external changes relevant to: {goal}",
        inputs=("goal", "source_connector_registry"),
        outputs=("signals", "source_list", "freshness_notes"),
        allowed_tools=("web_search", "web_fetch", "rss_fetch"),
        forbidden_tools=("file_write", "shell_exec"),
        max_iterations=2,
        max_model_calls=2,
        max_cost_units=3,
    )


def _business_opportunity_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="BusinessOpportunityAgent",
        role="market_fit_analyst",
        objective=f"Translate the goal into client pain, MVP offer and pricing hypotheses: {goal}",
        inputs=("signals", "project_capabilities", "constraints"),
        outputs=("target_niche", "client_pain", "mvp_offer", "validation_plan"),
        allowed_tools=("file_read",),
        max_model_calls=2,
        max_cost_units=4,
    )


def _coder_signal_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="CoderSignalAgent",
        role="code_change_scout",
        objective=f"Identify whether the goal needs code changes, tests or repair proposals: {goal}",
        inputs=("goal", "repo_context", "test_results"),
        outputs=("candidate_files", "test_plan", "risk_notes"),
        allowed_tools=("file_read", "read_logs", "run_tests", "diff_file"),
        forbidden_tools=("file_write", "shell_exec"),
        model_role="repair_proposal",
        max_model_calls=2,
        max_cost_units=5,
        verifier="SelfRepairVerifier",
    )


def _architecture_impact_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="ArchitectureImpactAgent",
        role="architecture_reviewer",
        objective=f"Map the goal to architecture layers and identify missing contracts: {goal}",
        inputs=("goal", "architecture_docs", "current_code_state"),
        outputs=("affected_layers", "missing_contracts", "recommended_next_mvp"),
        allowed_tools=("file_read",),
        max_model_calls=2,
        max_cost_units=4,
    )


def _verifier_agent(goal: str) -> SubagentContract:
    return SubagentContract(
        name="VerifierAgent",
        role="result_checker",
        objective=f"Check subagent outputs for evidence, conflicts, budget and scope: {goal}",
        inputs=("subagent_outputs", "source_registry", "budget_summary"),
        outputs=("verification_report", "unverified_items", "go_no_go"),
        allowed_tools=("file_read",),
        model_role="verifier",
        max_model_calls=1,
        max_cost_units=2,
    )
