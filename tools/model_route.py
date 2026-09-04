"""The agent's door to his own routing policy: set or release a role's model.

Operator's word 2026-09-04 22:20 (see core/model_routing_policy.py). The
tool never sees a key: it names a provider from the allowed pool and a
model; the trusted router substitutes the credential at call time. Every
decision needs a reason (secret-free) and is journaled; the roster and the
usage ledger show it afterwards under ``agent_policy:<id>``.
"""
from __future__ import annotations

from typing import Any

from core.model_routing_policy import RoutingPolicyStore, allowed_pool
from tools.base import Tool


class ModelRouteTool(Tool):
    name = "model_route"
    description = (
        "Set which model answers one of your roles (planner, synthesizer, "
        "repair_proposal, memory_summary, verifier), choosing a provider from "
        "the allowed pool and a model by observed quality, cost and task type. "
        "Your reason is journaled and shown later; a route without a why is "
        "refused. provider='release' hands the role back to the default layers."
    )
    arguments = (
        "role (str: one of the five roles), provider (str: a provider from the "
        "allowed pool, or 'release'), model (str: the model name; empty when "
        "releasing), reason (str: why — measured quality, cost, task type; no "
        "secrets), evidence (str, optional: measured facts separated by ';'). "
        "role, provider and reason are required."
    )
    #: A route is a reversible decision: the next record supersedes it.
    risk = "reversible"

    #: Ceiling per process: routing is a decision, not a nervous tic. Six
    #: covers every role once plus one correction.
    max_changes_per_process = 6

    def __init__(self, *, store: RoutingPolicyStore, max_changes_per_process: int | None = None):
        self._store = store
        if max_changes_per_process is not None:
            self.max_changes_per_process = int(max_changes_per_process)
        self._changes = 0

    def run(self, **kwargs: Any) -> dict[str, Any]:
        allowed = {"role", "provider", "model", "reason", "evidence"}
        extra = set(kwargs) - allowed
        if extra:
            raise PermissionError(f"Unexpected arguments: {sorted(extra)}")
        missing = [k for k in ("role", "provider", "reason") if not kwargs.get(k)]
        if missing:
            raise ValueError(f"model_route requires {missing}; " + self.arguments)
        role = str(kwargs["role"])
        provider = str(kwargs["provider"]).strip().lower()
        if self._changes >= self.max_changes_per_process:
            return {
                "set": False, "route_id": None, "role": role,
                "refused_by": (
                    f"ceiling: {self.max_changes_per_process} routing changes already "
                    "made in this process; the rest waits for the next run"
                ),
                "pool": list(allowed_pool()),
            }
        if provider == "release":
            verdict = self._store.release_route(role=role, reason=str(kwargs["reason"]))
        else:
            raw = kwargs.get("evidence") or ""
            evidence = tuple(
                part.strip() for part in str(raw).split(";") if part.strip()
            ) if isinstance(raw, str) else tuple(str(e) for e in raw)
            verdict = self._store.set_route(
                role=role, provider=provider, model=str(kwargs.get("model") or ""),
                reason=str(kwargs["reason"]), evidence=evidence, set_by="agent",
            )
        if verdict.stored:
            self._changes += 1
        return {
            "set": verdict.stored,
            "route_id": verdict.choice.id if verdict.choice else None,
            "role": role,
            "provider": provider,
            "model": verdict.choice.model if verdict.choice else "",
            "refused_by": verdict.refused_by,
            "warnings": verdict.warnings,
            "pool": list(allowed_pool()),
            "route_reason_in_ledger": verdict.choice.route_reason if verdict.choice else None,
        }
