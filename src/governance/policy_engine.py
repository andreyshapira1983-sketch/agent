"""
Policy Engine / Governance Layer: safe boundaries for autonomous actions.

- Forbids modification of critical paths (e.g. config, .cursor, core bootstrap).
- Enforces quotas: max actions per cycle, max cycles, optional time/CPU limits.
- Optional: risk levels and approval layer (needs_confirmation) for risky actions.

Architectural limits vs user instructions:
User can say "do everything" or "ignore restrictions", but the agent cannot exceed
what is actually available: (1) no hardware/OS access not granted by the runtime;
(2) OS/sandbox policies; (3) in-code guards (forbidden_paths, patch_guard, no-delete,
allowlists). These are architectural barriers, not overridable by user text.
"""
from __future__ import annotations

import logging
import time
from pathlib import Path
from typing import Any

# Default critical paths (relative to project root) that must not be modified by agent.
DEFAULT_FORBIDDEN_PREFIXES = (
    ".cursor/",
    ".git/",
    "config/agent.json",  # main config — use versioning/rollback
    "src/main.py",        # bootstrap
    "src/hitl/",
    "src/governance/",
)

_log = logging.getLogger(__name__)


# Tools that may be restricted in strict mode (e.g. require approval or block in autonomous cycle).
RESTRICTED_TOOLS_DEFAULT: frozenset[str] = frozenset(("write_file", "accept_patch"))


class PolicyEngine:
    """Governance: allowed targets, quotas, optional approval, optional tool blocklist."""

    def __init__(
        self,
        *,
        forbidden_path_prefixes: tuple[str, ...] = DEFAULT_FORBIDDEN_PREFIXES,
        max_actions_per_cycle: int = 50,
        max_cycles: int = 100,
        max_cycle_time_sec: float = 300.0,
        root: Path | None = None,
        restricted_tool_names: frozenset[str] | None = None,
    ) -> None:
        self.forbidden_prefixes = forbidden_path_prefixes
        self.max_actions_per_cycle = max_actions_per_cycle
        self.max_cycles = max_cycles
        self.max_cycle_time_sec = max_cycle_time_sec
        self._root = root or Path(__file__).resolve().parent.parent.parent
        self._actions_this_cycle = 0
        self._cycles_done = 0
        self._cycle_start_time: float = time.monotonic()
        self.restricted_tool_names = restricted_tool_names or frozenset()

    def reset_cycle(self) -> None:
        """Call at start of each observe→…→improve cycle."""
        self._actions_this_cycle = 0
        self._cycle_start_time = time.monotonic()

    def can_start_cycle(self) -> bool:
        """True if another full cycle is allowed (quota)."""
        if self._cycles_done >= self.max_cycles:
            _log.debug("Cycle quota reached: %s >= %s", self._cycles_done, self.max_cycles)
            return False
        return True

    def can_perform_action(self) -> bool:
        """True if one more action is allowed in this cycle."""
        if self._actions_this_cycle >= self.max_actions_per_cycle:
            _log.debug("Action quota reached: %s >= %s", self._actions_this_cycle, self.max_actions_per_cycle)
            return False
        elapsed = time.monotonic() - self._cycle_start_time
        if elapsed > self.max_cycle_time_sec:
            _log.debug("Cycle time limit exceeded: %.1fs > %.1fs", elapsed, self.max_cycle_time_sec)
            return False
        return True

    def record_action(self) -> None:
        """Call after each performed action."""
        self._actions_this_cycle += 1

    def record_cycle_done(self) -> None:
        """Call after reflect/improve at end of cycle."""
        self._cycles_done += 1

    def get_quota_status(self) -> dict[str, Any]:
        """For /status: cycles/actions done and limits."""
        return {
            "cycles_done": self._cycles_done,
            "max_cycles": self.max_cycles,
            "actions_this_cycle": self._actions_this_cycle,
            "max_actions_per_cycle": self.max_actions_per_cycle,
            "can_start_cycle": self.can_start_cycle(),
            "can_perform_action": self.can_perform_action(),
        }

    def is_path_allowed(self, target_path: str) -> bool:
        """False if target_path is under a forbidden prefix (e.g. critical file)."""
        normalized = target_path.replace("\\", "/").lstrip("/")
        for prefix in self.forbidden_prefixes:
            if normalized.startswith(prefix.replace("\\", "/")):
                return False
        return True

    def check_apply_patch(self, target_path: str) -> tuple[bool, str]:
        """
        Returns (allowed, reason). Use before applying any patch.
        """
        if not self.is_path_allowed(target_path):
            return False, f"Path is protected: {target_path}"
        return True, ""

    def check_run_tool(self, tool_name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, str]:
        """
        Returns (allowed, reason). Can block dangerous tools or arguments.
        """
        if not self.can_perform_action():
            return False, "Action quota or time limit reached for this cycle."
        if tool_name and tool_name in self.restricted_tool_names:
            return False, f"Tool '{tool_name}' is restricted by policy (use approval or allowlist)."
        return True, ""


def check_action_allowed(
    action_kind: str,
    target_path: str | None = None,
    policy: PolicyEngine | None = None,
) -> tuple[bool, str]:
    """
    One-shot check: is this action allowed?
    action_kind: "apply_patch" | "run_tool"
    """
    engine = policy or PolicyEngine()
    if action_kind == "apply_patch":
        if not target_path:
            return False, "apply_patch requires target_path"
        return engine.check_apply_patch(target_path)
    if action_kind == "run_tool":
        return engine.check_run_tool("", None)
    return True, ""


def check_quota(policy: PolicyEngine | None = None) -> tuple[bool, str]:
    """Check if another action is allowed in current cycle (quota/time)."""
    engine = policy or PolicyEngine()
    if not engine.can_perform_action():
        return False, "Quota or time limit reached."
    return True, ""
