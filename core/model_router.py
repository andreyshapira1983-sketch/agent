"""Role-based model routing.

The agent core should not be married to one model. This module keeps that
choice behind a small routing layer so planner, synthesis, repair proposal,
and memory summarisation can move independently as better models appear.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from enum import Enum
from typing import Any, Callable, Iterable, Mapping

from core.llm import LLM


class ModelRole(str, Enum):
    """Stable model roles used by the runtime."""

    PLANNER = "planner"
    SYNTHESIZER = "synthesizer"
    REPAIR_PROPOSAL = "repair_proposal"
    MEMORY_SUMMARY = "memory_summary"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class ModelRoute:
    """One role -> provider/model mapping.

    `provider` or `model` may be None, in which case the router falls back to
    its default route (`AGENT_PROVIDER` / `AGENT_MODEL`, then LLM defaults).
    """

    role: str
    provider: str | None = None
    model: str | None = None
    reason: str = "default"

    def summary(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "reason": self.reason,
        }


LLMFactory = Callable[[str | None, str | None], Any]


_ROLE_ENV_PREFIXES: dict[ModelRole, tuple[str, ...]] = {
    ModelRole.PLANNER: ("AGENT_PLANNER",),
    ModelRole.SYNTHESIZER: ("AGENT_SYNTHESIZER", "AGENT_ANSWER"),
    ModelRole.REPAIR_PROPOSAL: ("AGENT_REPAIR", "AGENT_REPAIR_PROPOSAL"),
    ModelRole.MEMORY_SUMMARY: ("AGENT_MEMORY", "AGENT_MEMORY_SUMMARY"),
    ModelRole.VERIFIER: ("AGENT_VERIFIER",),
}


def _clean_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _normalise_provider(provider: str | None) -> str | None:
    if provider is None:
        return None
    provider = provider.strip().lower()
    return provider or None


def _coerce_role(role: ModelRole | str) -> str:
    if isinstance(role, ModelRole):
        return role.value
    value = str(role).strip()
    if not value:
        raise ValueError("model role must be non-empty")
    return value


def _llm_factory(provider: str | None, model: str | None) -> LLM:
    return LLM(provider=provider, model=model)


class ModelRouter:
    """Resolve model roles to reusable LLM-like objects."""

    def __init__(
        self,
        *,
        default_provider: str | None = None,
        default_model: str | None = None,
        routes: Mapping[ModelRole | str, ModelRoute] | None = None,
        llm_factory: LLMFactory | None = None,
        static_llm: Any | None = None,
    ):
        self.default_provider = _normalise_provider(default_provider)
        self.default_model = default_model.strip() if isinstance(default_model, str) else default_model
        self._routes: dict[str, ModelRoute] = {}
        for role, route in (routes or {}).items():
            role_key = _coerce_role(role)
            self._routes[role_key] = ModelRoute(
                role=role_key,
                provider=_normalise_provider(route.provider),
                model=route.model.strip() if isinstance(route.model, str) else route.model,
                reason=route.reason,
            )
        self._llm_factory = llm_factory or _llm_factory
        self._static_llm = static_llm
        self._cache: dict[tuple[str | None, str | None], Any] = {}

    @classmethod
    def single(cls, llm: Any) -> "ModelRouter":
        """Compatibility mode: every role returns the same LLM object."""

        return cls(static_llm=llm)

    @classmethod
    def from_env(cls, *, llm_factory: LLMFactory | None = None) -> "ModelRouter":
        """Build a router from default and role-specific environment vars.

        Defaults:
            AGENT_PROVIDER / AGENT_MODEL

        Role overrides:
            AGENT_PLANNER_PROVIDER / AGENT_PLANNER_MODEL
            AGENT_SYNTHESIZER_PROVIDER / AGENT_SYNTHESIZER_MODEL
            AGENT_REPAIR_PROVIDER / AGENT_REPAIR_MODEL
            AGENT_REPAIR_PROPOSAL_PROVIDER / AGENT_REPAIR_PROPOSAL_MODEL
            AGENT_MEMORY_PROVIDER / AGENT_MEMORY_MODEL
            AGENT_VERIFIER_PROVIDER / AGENT_VERIFIER_MODEL
        """

        routes: dict[ModelRole, ModelRoute] = {}
        for role, prefixes in _ROLE_ENV_PREFIXES.items():
            provider = None
            model = None
            chosen_prefix = None
            for prefix in prefixes:
                provider = _clean_env(f"{prefix}_PROVIDER")
                model = _clean_env(f"{prefix}_MODEL")
                if provider or model:
                    chosen_prefix = prefix
                    break
            if chosen_prefix:
                routes[role] = ModelRoute(
                    role=role.value,
                    provider=_normalise_provider(provider),
                    model=model,
                    reason=f"env:{chosen_prefix}",
                )
        return cls(
            default_provider=_clean_env("AGENT_PROVIDER"),
            default_model=_clean_env("AGENT_MODEL"),
            routes=routes,
            llm_factory=llm_factory,
        )

    def route_for(self, role: ModelRole | str) -> ModelRoute:
        role_key = _coerce_role(role)
        route = self._routes.get(role_key)
        if route is not None:
            return route
        return ModelRoute(
            role=role_key,
            provider=self.default_provider,
            model=self.default_model,
            reason="default",
        )

    def for_role(self, role: ModelRole | str) -> Any:
        """Return the LLM-like object for `role`, creating it once."""

        if self._static_llm is not None:
            return self._static_llm
        route = self.route_for(role)
        provider = route.provider or self.default_provider
        model = route.model or self.default_model
        cache_key = (provider, model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, model)
        return self._cache[cache_key]

    def routing_summary(
        self,
        roles: Iterable[ModelRole | str] | None = None,
    ) -> dict[str, dict[str, Any]]:
        """Human/log friendly snapshot of active role routes."""

        selected_roles = tuple(roles or ModelRole)
        summary: dict[str, dict[str, Any]] = {}
        for role in selected_roles:
            role_key = _coerce_role(role)
            route = self.route_for(role_key)
            llm = self.for_role(role_key)
            summary[role_key] = {
                "provider": getattr(llm, "provider", route.provider),
                "model": getattr(llm, "model", route.model),
                "reason": route.reason,
            }
        return summary

