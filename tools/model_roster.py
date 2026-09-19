"""`model_roster` — the agent asks what models it has. Read-only; no key values."""
from __future__ import annotations

from typing import Any

from core.model_roster import model_roster
from tools.base import Tool


class ModelRosterTool(Tool):
    name = "model_roster"
    description = (
        "Show your own model providers as the router and the ledgers see them: "
        "which have a key, which roles they serve, whether they are healthy and "
        "why not, what they cost per 1k tokens, what was spent today and how much "
        "of the day's cost ceiling is left. Facts only; it never switches anything "
        "and never reveals a key."
    )
    arguments = "no arguments"
    risk = "read_only"

    def __init__(
        self, *, usage_ledger: Any = None, budget_ledger: Any = None,
        routing_policy: Any = None, workspace: Any = None,
    ):
        self._usage_ledger = usage_ledger
        self._budget_ledger = budget_ledger
        self._routing_policy = routing_policy
        self._workspace = workspace

    def run(self, **kwargs):
        if kwargs:
            raise PermissionError(f"Unexpected arguments: {sorted(kwargs)}")
        return model_roster(
            usage_ledger=self._usage_ledger, budget_ledger=self._budget_ledger,
            routing_policy=self._routing_policy, workspace=self._workspace,
        )

    def validate_output(self, output):
        if not isinstance(output, dict) or not isinstance(output.get("providers"), list):
            return False, ["roster must be a dict with a providers list"]
        for p in output["providers"]:
            for key in ("provider", "key_present", "roles", "health"):
                if key not in p:
                    return False, [f"provider row lacks {key}"]
        return True, []
