"""Role-based model routing.

The agent core should not be married to one model. This module keeps that
choice behind a small routing layer so planner, synthesis, repair proposal,
and memory summarisation can move independently as better models appear.
"""
from __future__ import annotations

import json
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


@dataclass(frozen=True)
class ModelSpec:
    """One model option in the local model registry.

    New models under an already-supported provider do not require code
    changes: add them through `AGENT_MODEL_REGISTRY_JSON` with the roles they
    should serve. New providers still need an LLM adapter.
    """

    id: str
    provider: str
    model: str
    roles: tuple[str, ...] = ("*",)
    quality_tier: str = "standard"
    cost_tier: str = "unknown"
    context_window: int | None = None
    enabled: bool = True
    source: str = "builtin"
    notes: str = ""

    def supports(self, role: ModelRole | str) -> bool:
        role_key = _coerce_role(role)
        return self.enabled and ("*" in self.roles or role_key in self.roles)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "provider": self.provider,
            "model": self.model,
            "roles": list(self.roles),
            "quality_tier": self.quality_tier,
            "cost_tier": self.cost_tier,
            "context_window": self.context_window,
            "enabled": self.enabled,
            "source": self.source,
            "notes": self.notes,
            "provider_supported": self.provider in SUPPORTED_PROVIDERS,
        }


SUPPORTED_PROVIDERS = frozenset({"anthropic", "openai", "huggingface", "mock"})

_QUALITY_SCORE = {
    "frontier": 5,
    "high": 4,
    "standard": 3,
    "small": 2,
    "cheap": 1,
    "unknown": 0,
}

_COST_SCORE = {
    "free": 5,
    "low": 4,
    "medium": 3,
    "high": 2,
    "unknown": 1,
}


class ModelRegistry:
    """Catalog of known/available models.

    Builtins are only documentation/default choices. Custom JSON entries are
    selectable by the router, which is what lets a newly released model be
    added without editing Python code.
    """

    def __init__(self, specs: Iterable[ModelSpec] | None = None):
        self._specs = tuple(specs or ())

    @classmethod
    def from_env(cls) -> "ModelRegistry":
        specs = list(_builtin_model_specs())
        specs.extend(_custom_model_specs_from_env())
        return cls(specs)

    def list(self, *, include_disabled: bool = True) -> tuple[ModelSpec, ...]:
        if include_disabled:
            return self._specs
        return tuple(spec for spec in self._specs if spec.enabled)

    def custom_specs(self) -> tuple[ModelSpec, ...]:
        return tuple(spec for spec in self._specs if spec.source.startswith("env"))

    def best_for_role(self, role: ModelRole | str) -> ModelSpec | None:
        candidates = [
            spec for spec in self.custom_specs()
            if spec.supports(role) and spec.provider in SUPPORTED_PROVIDERS
        ]
        if not candidates:
            return None
        role_key = _coerce_role(role)
        return max(candidates, key=lambda spec: _model_score(spec, role_key))

    def to_payload(self) -> dict[str, Any]:
        return {
            "models": [spec.to_dict() for spec in self._specs],
            "custom_count": len(self.custom_specs()),
            "supported_providers": sorted(SUPPORTED_PROVIDERS),
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


def _builtin_model_specs() -> tuple[ModelSpec, ...]:
    return (
        ModelSpec(
            id="anthropic-default",
            provider="anthropic",
            model="claude-sonnet-4-5",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="high",
            cost_tier="medium",
            source="builtin",
            notes="default reasoning/coding route",
        ),
        ModelSpec(
            id="openai-default-small",
            provider="openai",
            model="gpt-4o-mini",
            roles=(ModelRole.SYNTHESIZER.value, ModelRole.MEMORY_SUMMARY.value),
            quality_tier="standard",
            cost_tier="low",
            source="builtin",
            notes="cheap synthesis/summary fallback",
        ),
        ModelSpec(
            id="hf-default",
            provider="huggingface",
            model="meta-llama/Llama-3.3-70B-Instruct",
            roles=(ModelRole.SYNTHESIZER.value, ModelRole.MEMORY_SUMMARY.value),
            quality_tier="standard",
            cost_tier="low",
            source="builtin",
            notes="HuggingFace router-compatible model",
        ),
        ModelSpec(
            id="mock",
            provider="mock",
            model="mock-1",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="cheap",
            cost_tier="free",
            source="builtin",
            notes="offline deterministic test model",
        ),
    )


def _custom_model_specs_from_env() -> tuple[ModelSpec, ...]:
    raw = _clean_env("AGENT_MODEL_REGISTRY_JSON")
    if not raw:
        return ()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"AGENT_MODEL_REGISTRY_JSON is not valid JSON: {exc}") from exc
    if isinstance(data, dict):
        items = data.get("models", [])
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("AGENT_MODEL_REGISTRY_JSON must be a list or {'models': [...]}")
    specs: list[ModelSpec] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ValueError(f"model registry item {idx} must be an object")
        provider = _normalise_provider(str(item.get("provider", "") or ""))
        model = str(item.get("model", "") or "").strip()
        if not provider or not model:
            raise ValueError(f"model registry item {idx} requires provider and model")
        raw_roles = item.get("roles", ["*"])
        if isinstance(raw_roles, str):
            roles = tuple(r.strip() for r in raw_roles.split(",") if r.strip())
        elif isinstance(raw_roles, list):
            roles = tuple(str(r).strip() for r in raw_roles if str(r).strip())
        else:
            raise ValueError(f"model registry item {idx} roles must be string or list")
        specs.append(
            ModelSpec(
                id=str(item.get("id") or f"env:{provider}:{model}").strip(),
                provider=provider,
                model=model,
                roles=roles or ("*",),
                quality_tier=str(item.get("quality_tier") or "standard").strip().lower(),
                cost_tier=str(item.get("cost_tier") or "unknown").strip().lower(),
                context_window=(
                    int(item["context_window"])
                    if item.get("context_window") is not None
                    else None
                ),
                enabled=bool(item.get("enabled", True)),
                source="env:AGENT_MODEL_REGISTRY_JSON",
                notes=str(item.get("notes") or ""),
            )
        )
    return tuple(specs)


def _model_score(spec: ModelSpec, role_key: str) -> tuple[int, int, int, int, str]:
    exact_role = 1 if role_key in spec.roles else 0
    quality = _QUALITY_SCORE.get(spec.quality_tier, _QUALITY_SCORE["unknown"])
    cost = _COST_SCORE.get(spec.cost_tier, _COST_SCORE["unknown"])
    context = spec.context_window or 0
    # Deterministic tie-breaker keeps repeated runs stable.
    return (exact_role, quality, cost, context, spec.id)


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
        registry: ModelRegistry | None = None,
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
        self.registry = registry or ModelRegistry()

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
            registry=ModelRegistry.from_env(),
        )

    def route_for(self, role: ModelRole | str) -> ModelRoute:
        role_key = _coerce_role(role)
        route = self._routes.get(role_key)
        if route is not None:
            return route
        registry_spec = self.registry.best_for_role(role_key)
        if registry_spec is not None:
            return ModelRoute(
                role=role_key,
                provider=registry_spec.provider,
                model=registry_spec.model,
                reason=f"registry:{registry_spec.id}",
            )
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

    def registry_summary(self) -> dict[str, Any]:
        return self.registry.to_payload()
