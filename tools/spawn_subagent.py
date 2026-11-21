"""spawn_subagent tool: delegate one independent sub-task to a bounded, read-only child agent."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.subagent_contract import CanonicalSubagentContract
from core.subagent_runner import (
    _SAFE_SUBAGENT_TOOLS,
    SubAgentRunner,
    SubAgentRunResult,
)
from tools.base import Tool, ToolRegistry

# Length limits so crafted role/objective payloads cannot flood the child's context.
_MAX_ROLE_LEN = 120
_MAX_OBJECTIVE_LEN = 800
_MAX_CONTEXT_LEN = 2000
_MAX_SPAWN_PER_PLAN = 3   # documented in planner system prompt


class SpawnSubagentTool(Tool):
    """Spawn a bounded sub-agent to handle one independent parallel sub-task."""

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

    def run_contract(
        self,
        contract: CanonicalSubagentContract,
        *,
        approved: bool = False,
        context: str = "",
    ) -> SubAgentRunResult:
        """Execute an already validated canonical approval contract."""
        return self._runner.run_contract(
            contract,
            approved=approved,
            context=context,
        )

    def run(
        self,
        role: str,
        objective: str,
        context: str = "",
        allowed_tools: list[str] | None = None,
        contract_name: str | None = None,
        why: str = "",
        expect: str = "",
        expect_sources: int | None = None,
    ) -> str:
        """Run a sub-agent and return its answer as evidence text.

        `why` and `expect` are required; `allowed_tools` is filtered to the safe read-only set.
        """
        # ── input validation ─────────────────────────────────────────
        if not (str(why or "").strip() and str(expect or "").strip()):
            raise ValueError("spawn_subagent: 'why' (why a sub-agent, not a direct tool call) "
                             "and 'expect' (what it will return) are required")
        self._validate_role(role)
        self._validate_objective(objective)
        self._validate_context(context)
        validated_tools = self._validate_allowed_tools(allowed_tools)
        name = self._resolve_contract_name(contract_name, role)

        # ── delegate to runner ───────────────────────────────────────
        from datetime import datetime, timezone

        started = datetime.now(timezone.utc)
        result = self._runner.run(
            contract_name=name,
            role=role,
            objective=objective,
            context=context,
            allowed_tools=validated_tools,
        )

        # Quarantine: the finding is kept with provenance and `authority: none`; it is not memory.
        from core.subagent_quarantine import quarantine_finding

        quarantine_finding(self.workspace_root, result)
        self._record_prediction(name, role, objective, why, expect, result,
                                expect_sources=expect_sources, started=started)

        # The child's external evidences ride beside the text into the parent's evidence chain.
        stash = list(getattr(self, "last_child_evidences", None) or [])
        stash.extend(getattr(result, "external_evidences", ()) or ())
        self.last_child_evidences = stash
        # The synthesiser cites this tool output as [subagent:<name>].
        return (result.to_evidence_text()
                + f"\n  why (parent)   : {str(why).strip()[:400]}"
                + f"\n  expected       : {str(expect).strip()[:400]}")

    def _record_prediction(self, name: str, role: str, objective: str, why: str, expect: str,
                           result: SubAgentRunResult, *, expect_sources: int | None = None,
                           started: Any = None) -> None:
        """Record the prediction beside the outcome and compare (core/subagent_predictions.py)."""
        from core.subagent_predictions import (
            child_tool_calls,
            compare,
            predicted_sources,
            record,
        )
        from core.usd_spend import usd_since

        expected, how = predicted_sources(str(expect), expect_sources)
        status = str(getattr(result, "status", "") or "")
        sources = int(getattr(result, "external_evidence_count", 0) or 0)
        try:
            usd = usd_since(self.workspace_root, started) if started is not None else None
        except ValueError:  # spend journal unreadable: the prediction row is still written
            usd = None
        record(self.workspace_root, {
            "name": name, "role": role, "objective": objective[:400], "why": str(why)[:400],
            "expect": str(expect)[:400], "expected_sources": expected, "prediction": how,
            "status": status, "sources": sources,
            "answer_chars": len(str(getattr(result, "answer", "") or "")),
            "tool_calls": child_tool_calls(self.log_dir, str(getattr(result, "trace_id", ""))),
            "usd": usd,
            "met": compare(expected, sources, status),
        })

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
        # Unsafe or unknown names are dropped, so naming a tool cannot escalate privileges.
        safe = [t for t in allowed_tools if isinstance(t, str) and t in _SAFE_SUBAGENT_TOOLS]
        return safe or None   # None → runner uses all safe defaults

    @staticmethod
    def _resolve_contract_name(contract_name: str | None, role: str) -> str:
        """Derive an ASCII contract name from the provided value or from role."""
        if contract_name and isinstance(contract_name, str) and contract_name.strip():
            candidate = contract_name.strip()
            if len(candidate) <= 40 and candidate.isascii():
                return candidate
        # Fallback: turn role into an ASCII slug
        slug = "".join(c if c.isascii() and (c.isalnum() or c in "_-") else "_" for c in role)
        return slug[:40].strip("_") or "SubAgent"

    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, str):
            return False, [f"expected str from spawn_subagent, got {type(output).__name__}"]
        if not output.strip():
            return False, ["spawn_subagent returned empty output"]
        return True, []
