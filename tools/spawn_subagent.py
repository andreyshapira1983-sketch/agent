"""spawn_subagent tool — agent-as-tool pattern for parallel sub-task delegation.

When the planner determines that a task has multiple INDEPENDENT sub-objectives
that benefit from isolation (e.g. "compare X and Y", "research A while
analysing file B"), it emits ``spawn_subagent`` steps in its plan.

Each call to ``SpawnSubagentTool.run()`` creates a bounded child ``AgentLoop``
via ``SubAgentRunner``, runs it with the given objective, and returns the
child's answer as a string that the parent loop treats as Evidence.

Safety guarantees
-----------------
1. ``spawn_subagent`` is NEVER included in child registries → no recursion.
2. Only tools in ``_SAFE_SUBAGENT_TOOLS`` (all read-only) reach the child.
3. ``shell_exec`` and ``file_write`` are always blocked from sub-agents.
4. Each sub-agent gets at most one planning attempt (``max_replan_attempts=1``).
5. ``role`` and ``objective`` inputs are validated before the child is started.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.subagent_runner import SubAgentRunner, _SAFE_SUBAGENT_TOOLS
from tools.base import Tool, ToolRegistry

# Hard limits on string field lengths to prevent prompt injection via
# crafted role/objective payloads that overflow context.
_MAX_ROLE_LEN = 120
_MAX_OBJECTIVE_LEN = 800
_MAX_CONTEXT_LEN = 2000
_MAX_SPAWN_PER_PLAN = 3   # documented in planner system prompt


class SpawnSubagentTool(Tool):
    """Spawn a bounded sub-agent to handle one independent parallel sub-task.

    The tool is registered in the parent loop's ToolRegistry.  It is NOT
    included in any child registry, so sub-agents cannot spawn further
    sub-agents (depth limit enforced structurally, not via a flag).

    Constructor parameters are the same dependencies that ``AgentLoop``
    itself uses — they are forwarded to ``SubAgentRunner`` which uses them
    to build the child loop.
    """

    name = "spawn_subagent"
    description = (
        "Spawn a bounded sub-agent to accomplish one independent parallel sub-task. "
        "Use ONLY when the request has multiple independent information domains. "
        "Sub-agents cannot spawn further sub-agents."
    )
    risk = "read_only"   # pure computation; no writes, no external side-effects

    def __init__(
        self,
        workspace_root: Path | str,
        policy: Any,          # core.policy.PolicyGate — Any to avoid top-level import
        model_router: Any,    # core.model_router.ModelRouter
        parent_registry: ToolRegistry,
        log_dir: Path | str,
    ) -> None:
        self.workspace_root = Path(workspace_root)
        self.policy = policy
        self.model_router = model_router
        self.parent_registry = parent_registry
        self.log_dir = Path(log_dir)
        self._runner = SubAgentRunner(
            workspace_root=self.workspace_root,
            policy=self.policy,
            model_router=self.model_router,
            parent_registry=self.parent_registry,
            log_dir=self.log_dir,
        )

    # ------------------------------------------------------------------
    # Tool.run() implementation
    # ------------------------------------------------------------------

    def run(
        self,
        role: str,
        objective: str,
        context: str = "",
        allowed_tools: list[str] | None = None,
        contract_name: str | None = None,
    ) -> str:
        """Execute a sub-agent and return its answer as a string.

        Parameters
        ----------
        role:
            Who the sub-agent is, e.g. "WebResearcher" or "FileAnalyst".
            Used in the child's system context and as the citation label.
        objective:
            A precise description of what the sub-agent must return.
            Be specific — vague objectives produce vague answers.
        context:
            Optional background information from the parent agent.
            Keep it focused; only include what the child actually needs.
        allowed_tools:
            Subset of safe tools the child may use.
            Must be a subset of: file_read, list_dir, web_search,
            web_fetch, rss_fetch, semantic_scholar_search, run_tests,
            read_logs, diff_file.
            Defaults to all of the above if None or empty.
        contract_name:
            Short identifier for this contract (ASCII, max 40 chars).
            Defaults to a slug of `role` if not provided.
        """
        # ── input validation ─────────────────────────────────────────
        self._validate_role(role)
        self._validate_objective(objective)
        self._validate_context(context)
        validated_tools = self._validate_allowed_tools(allowed_tools)
        name = self._resolve_contract_name(contract_name, role)

        # ── delegate to runner ───────────────────────────────────────
        result = self._runner.run(
            contract_name=name,
            role=role,
            objective=objective,
            context=context,
            allowed_tools=validated_tools,
        )

        # Return evidence text — the parent loop stores this as the tool
        # output and the synthesiser cites it via [subagent:<name>].
        return result.to_evidence_text()

    # ------------------------------------------------------------------
    # Validation helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _validate_role(role: str) -> None:
        if not isinstance(role, str) or not role.strip():
            raise ValueError("spawn_subagent: 'role' must be a non-empty string")
        if len(role) > _MAX_ROLE_LEN:
            raise ValueError(
                f"spawn_subagent: 'role' exceeds {_MAX_ROLE_LEN} characters"
            )

    @staticmethod
    def _validate_objective(objective: str) -> None:
        if not isinstance(objective, str) or not objective.strip():
            raise ValueError("spawn_subagent: 'objective' must be a non-empty string")
        if len(objective) > _MAX_OBJECTIVE_LEN:
            raise ValueError(
                f"spawn_subagent: 'objective' exceeds {_MAX_OBJECTIVE_LEN} characters"
            )

    @staticmethod
    def _validate_context(context: str) -> None:
        if not isinstance(context, str):
            raise ValueError("spawn_subagent: 'context' must be a string")
        if len(context) > _MAX_CONTEXT_LEN:
            raise ValueError(
                f"spawn_subagent: 'context' exceeds {_MAX_CONTEXT_LEN} characters"
            )

    @staticmethod
    def _validate_allowed_tools(allowed_tools: list[str] | None) -> list[str] | None:
        """Return the validated (and filtered to safe set) tool list, or None."""
        if allowed_tools is None:
            return None
        if not isinstance(allowed_tools, list):
            raise ValueError("spawn_subagent: 'allowed_tools' must be a list or null")
        # Filter silently to the safe set — the LLM cannot escalate privileges
        # by naming an unsafe tool; unknown / unsafe names are dropped quietly.
        safe = [t for t in allowed_tools if isinstance(t, str) and t in _SAFE_SUBAGENT_TOOLS]
        return safe if safe else None   # None → runner uses all safe defaults

    @staticmethod
    def _resolve_contract_name(contract_name: str | None, role: str) -> str:
        """Derive an ASCII contract name from the provided value or from role."""
        if contract_name and isinstance(contract_name, str) and contract_name.strip():
            candidate = contract_name.strip()
            if len(candidate) <= 40 and candidate.isascii():
                return candidate
        # Fallback: turn role into an ASCII slug
        slug = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in role)
        slug = slug[:40].strip("_") or "SubAgent"
        return slug

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str from spawn_subagent, got {type(output).__name__}"]
        if not output.strip():
            return False, ["spawn_subagent returned empty output"]
        return True, []
