"""Role-based model routing."""
from __future__ import annotations

import errno
import json
import os
import time
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, replace
from enum import StrEnum
from pathlib import Path
from typing import Any

from core.llm import LLM, accepted_flags
from core.model_routing_policy import agent_policy_route, drop_clients_if_policy_moved
from core.model_usage import (
    _KEY_CLASS_TEXT_MARKERS,
    ModelUsageLedger,
    cache_from_llm,
    usage_from_llm_or_estimate,
    utc_now_iso,
)


class ModelRole(StrEnum):
    """Stable model roles used by the runtime."""

    PLANNER = "planner"
    SYNTHESIZER = "synthesizer"
    REPAIR_PROPOSAL = "repair_proposal"
    MEMORY_SUMMARY = "memory_summary"
    VERIFIER = "verifier"


@dataclass(frozen=True)
class ModelRoute:
    """One role -> provider/model mapping; None falls back to the default route (AGENT_PROVIDER / AGENT_MODEL, then LLM defaults)."""

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
    """One model option in the local model registry."""

    id: str
    provider: str
    model: str
    roles: tuple[str, ...] = ("*",)
    quality_tier: str = "standard"
    cost_tier: str = "unknown"
    context_window: int | None = None
    enabled: bool = True
    requires_env: tuple[str, ...] = ()
    source: str = "builtin"
    notes: str = ""

    def supports(self, role: ModelRole | str) -> bool:
        role_key = _coerce_role(role)
        return self.enabled and ("*" in self.roles or role_key in self.roles)

    def requirements_satisfied(self) -> bool:
        return all(_clean_env(name) for name in self.requires_env)

    def provider_supported(self) -> bool:
        return self.provider in SUPPORTED_PROVIDERS

    def selectable(self, *, allow_mock: bool, require_available: bool) -> bool:
        if not self.enabled or not self.provider_supported():
            return False
        if self.provider == "mock" and not allow_mock:
            return False
        return not (require_available and not self.requirements_satisfied())

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
            "requires_env": list(self.requires_env),
            "available": self.requirements_satisfied(),
            "source": self.source,
            "notes": self.notes,
            "provider_supported": self.provider_supported(),
        }


SUPPORTED_PROVIDERS = frozenset({
    "anthropic",
    "openai",
    "deepseek",
    "huggingface",
    "local",
    "mock",
})

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

_COST_RANK = {
    "free": 0,
    "low": 1,
    "medium": 2,
    "high": 3,
    "unknown": 4,
}

_DEFAULT_PROVIDER_ENV: dict[str, tuple[str, ...]] = {
    "anthropic": ("ANTHROPIC_API_KEY",),
    "openai": ("OPENAI_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "huggingface": ("HF_TOKEN",),
    "local": ("LOCAL_LLM_BASE_URL", "LOCAL_LLM_MODEL"),
    "mock": (),
}


# Ordered provider preference per complexity tier among supported providers (`local` via AGENT_TIER_PROVIDERS_*); model names come from model_catalog.tier_model_for, never hardcoded. Keyed by ComplexityTier.value to avoid importing task_complexity.
_TIER_PROVIDER_PREF: dict[str, tuple[str, ...]] = {
    "light": ("huggingface", "openai"),
    "standard": ("openai",),
    "deep": ("anthropic",),
}

# Per-tier operator override, e.g. AGENT_TIER_PROVIDERS_LIGHT="huggingface,openai".
_TIER_PROVIDERS_ENV: dict[str, str] = {
    "light": "AGENT_TIER_PROVIDERS_LIGHT",
    "standard": "AGENT_TIER_PROVIDERS_STANDARD",
    "deep": "AGENT_TIER_PROVIDERS_DEEP",
}


def _tier_provider_prefs(tier_value: str) -> tuple[str, ...]:
    """Provider preference list for a tier, honoring the env override."""
    env_name = _TIER_PROVIDERS_ENV.get(tier_value)
    raw = os.getenv(env_name, "").strip() if env_name else ""
    if raw:
        return tuple(p.strip() for p in raw.split(",") if p.strip())
    return _TIER_PROVIDER_PREF.get(tier_value, ())


def _tier_providers_are_explicit(tier_value: str) -> bool:
    """True when the operator named the tier's providers via AGENT_TIER_PROVIDERS_*; only that overrides catalog discovery, so defaults never move silently."""
    env_name = _TIER_PROVIDERS_ENV.get(tier_value)
    return bool(os.getenv(env_name, "").strip()) if env_name else False


def _provider_has_credentials(provider: str) -> bool:
    """True when every required env var of *provider* is set; `mock` needs none, providers with no known requirement count as unavailable."""
    if provider not in _DEFAULT_PROVIDER_ENV:
        return False
    required = _DEFAULT_PROVIDER_ENV[provider]
    return all(os.getenv(var, "").strip() for var in required)


def _append_skipped(reason: str, skipped: list[str]) -> str:
    """Append skipped-provider details to a route reason, safely bounded."""
    if not skipped:
        return reason
    return reason + "|skipped:" + ",".join(skipped)


@dataclass(frozen=True)
class ModelSelectionPolicy:
    """How the router may choose from the model registry."""

    name: str = "conservative"
    max_cost_tier: str | None = None
    allow_mock: bool = False
    require_available: bool = True

    @classmethod
    def from_env(cls) -> ModelSelectionPolicy:
        raw_name = (
            _clean_env("AGENT_MODEL_POLICY")
            or _clean_env("AGENT_MODEL_SELECTION_POLICY")
            or "conservative"
        )
        name = raw_name.strip().lower().replace("-", "_")
        if name not in {"conservative", "balanced", "quality", "cost", "offline"}:
            raise ValueError(
                "AGENT_MODEL_POLICY must be one of: "
                "conservative, balanced, quality, cost, offline"
            )
        default_max_cost = {
            "conservative": None,
            "balanced": "medium",
            "quality": "high",
            "cost": "low",
            "offline": "free",
        }[name]
        max_cost = (_clean_env("AGENT_MODEL_MAX_COST") or default_max_cost)
        if max_cost is not None:
            max_cost = max_cost.strip().lower()
            if max_cost not in _COST_RANK:
                raise ValueError(
                    "AGENT_MODEL_MAX_COST must be one of: free, low, medium, high, unknown"
                )
        allow_mock = name == "offline" or _env_bool("AGENT_ALLOW_MOCK_ROUTING")
        require_available = not _env_bool("AGENT_MODEL_ALLOW_UNAVAILABLE")
        return cls(
            name=name,
            max_cost_tier=max_cost,
            allow_mock=allow_mock,
            require_available=require_available,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "max_cost_tier": self.max_cost_tier,
            "allow_mock": self.allow_mock,
            "require_available": self.require_available,
        }


class ModelRegistry:
    """Catalog of known models; builtins are documentation/defaults, custom JSON entries are selectable, so a new model needs no Python edit."""

    def __init__(self, specs: Iterable[ModelSpec] | None = None):
        self._specs = tuple(specs or ())

    @classmethod
    def from_env(cls) -> ModelRegistry:
        specs = list(_builtin_model_specs())
        specs.extend(_custom_model_specs_from_path())
        specs.extend(_custom_model_specs_from_env())
        return cls(specs)

    def list(self, *, include_disabled: bool = True) -> tuple[ModelSpec, ...]:
        if include_disabled:
            return self._specs
        return tuple(spec for spec in self._specs if spec.enabled)

    def custom_specs(self) -> tuple[ModelSpec, ...]:
        return tuple(spec for spec in self._specs if spec.source != "builtin")

    def best_for_role(
        self,
        role: ModelRole | str,
        *,
        policy: ModelSelectionPolicy | None = None,
    ) -> ModelSpec | None:
        policy = policy or ModelSelectionPolicy()
        source_specs = self.custom_specs() if policy.name == "conservative" else self._specs
        candidates = [
            spec for spec in source_specs
            if spec.supports(role)
            and spec.selectable(
                allow_mock=policy.allow_mock,
                require_available=policy.require_available,
            )
            and _within_cost_limit(spec, policy.max_cost_tier)
        ]
        if not candidates:
            return None
        role_key = _coerce_role(role)
        return max(
            candidates,
            key=lambda spec: _model_score_for_policy(spec, role_key, policy.name),
        )

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

#: Role names a router built by `from_env` can serve; exported so callers do not keep a drifting second copy of the list.
KNOWN_MODEL_ROLES: frozenset[str] = frozenset(role.value for role in ModelRole)


def ensure_known_model_role(value: str | None) -> None:
    """Reject a role name the agent's own router is not built to serve. `route_for` still serves an unknown role from the default model (a typo must not take down a live answer), so the mistake would otherwise show only in the ledger. The set is closed (`from_env` builds routes solely from `_ROLE_ENV_PREFIXES`); None and "" mean no preference, a whitespace-only string is refused."""
    if value is None:
        return
    role = value.strip()
    if not role:
        if value:
            raise ValueError("model_role must not be blank")
        return
    if role not in KNOWN_MODEL_ROLES:
        raise ValueError(
            "model_role must be one of " + ", ".join(sorted(KNOWN_MODEL_ROLES))
        )


class UsageTrackedLLM:
    """Small proxy that records one role-specific `complete()` call."""

    def __init__(
        self,
        llm: Any,
        *,
        role: str,
        route: ModelRoute,
        cost_tier: str,
        ledger: ModelUsageLedger,
        llm_factory: Callable[[str | None, str | None], Any] | None = None,
        reprice: Callable[[str, str], str] | None = None,
    ):
        self._llm = llm
        self.role = role
        self.cost_tier = cost_tier
        self.ledger = ledger
        # Re-prices the call when failover lands on a different provider/model, so a free local model failing over to a hosted one is not billed as free; None keeps the old behaviour.
        self._reprice = reprice
        # Factory that rebuilds the client on another provider during failover; None disables failover.
        self._llm_factory = llm_factory
        # Routing attribution integrity: the factory can heal an un-credentialed or vetoed route to another provider/model; record requested vs actual separately and stamp an honest downgrade reason when they diverge.
        self.requested_provider = route.provider
        self.requested_model = route.model
        actual_provider = getattr(llm, "provider", route.provider)
        actual_model = getattr(llm, "model", route.model)
        self.provider = actual_provider
        self.model = actual_model
        self.route = self._reconcile_route(route, actual_provider, actual_model)

    @staticmethod
    def _reconcile_route(
        route: ModelRoute,
        actual_provider: str | None,
        actual_model: str | None,
    ) -> ModelRoute:
        """Annotate ``route.reason`` when the live client diverges from the request; fires only when both are known and differ; requested provider/model stay on the route."""
        req_p = route.provider
        req_m = route.model
        provider_diverged = bool(req_p) and bool(actual_provider) and req_p != actual_provider
        model_diverged = bool(req_m) and bool(actual_model) and req_m != actual_model
        if not provider_diverged and not model_diverged:
            return route
        downgrade = (
            f"route_downgrade:requested={req_p or '?'}/{req_m or '?'}"
            f"->actual={actual_provider or '?'}/{actual_model or '?'}"
        )
        if downgrade in (route.reason or ""):
            return route
        reason = f"{route.reason}|{downgrade}" if route.reason else downgrade
        return replace(route, reason=reason)

    def attribution(self) -> dict[str, str | None]:
        """Structured routing attribution for diagnostics/logging."""
        actual_provider = getattr(self._llm, "provider", self.provider)
        actual_model = getattr(self._llm, "model", self.model)
        return {
            "role": self.role,
            "requested_provider": self.requested_provider,
            "requested_model": self.requested_model,
            "resolved_provider": self.route.provider,
            "resolved_model": self.route.model,
            "actual_provider": actual_provider,
            "actual_model": actual_model,
            "reason": self.route.reason,
        }

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

    def _provider_unhealthy(self, provider: str) -> str | None:
        """Ledger-derived provider health; None when unknown or disabled."""
        if self._llm_factory is None or not _provider_failover_enabled():
            return None
        try:
            return self.ledger.provider_unhealthy(provider)
        except Exception:  # noqa: BLE001 — health is advisory, never fatal
            return None

    def _next_provider_llm(self, tried: Sequence[str]) -> Any | None:
        """Build the substitute client on the next credentialed provider."""
        nxt = _next_failover_provider(tried)
        if nxt is None or self._llm_factory is None:
            return None
        try:
            from core.model_outcomes import substitute_model_with_reason

            model, why = substitute_model_with_reason(
                role=self.role, provider=nxt, current_model=self.model)
            self._failover_choice_reason = why
            return self._llm_factory(nxt, model)
        except Exception:  # noqa: BLE001 — pragma: no cover - defensive
            return None

    def _adopt_failover(self, replacement: Any) -> None:
        """Switch this proxy to the substitute client, repricing the tier."""
        self._llm = replacement
        self.provider = getattr(replacement, "provider", None) or ""
        self.model = getattr(replacement, "model", None) or ""
        if self._reprice is not None and self.provider:
            try:
                self.cost_tier = self._reprice(self.provider, self.model)
            except Exception:  # noqa: BLE001, S110 — pragma: no cover - defensive
                pass

    def _failover_llm(self, exc: BaseException, tried: Sequence[str]) -> Any | None:
        """Return a replacement LLM on another credentialed provider, or None; only for key/quota/auth errors, or downtime-looking errors while the provider is ``local``."""
        if self._llm_factory is None or not _provider_failover_enabled():
            return None
        provider = str(getattr(self._llm, "provider", self.route.provider) or "").lower()
        switchable = _is_switch_key_error(exc) or (
            provider == "local" and _is_local_unavailable_error(exc)
        )
        if not switchable:
            return None
        # Measurement first, tier map as floor (docs/CODE_NOTES.md); the reason for the choice is remembered and rides in route_reason so a silent decision can be investigated.
        return self._next_provider_llm(tried)

    def _call_llm(
        self,
        *,
        system: str,
        user: str,
        max_tokens: int,
        temperature: float,
        allow_continuation: bool,
        json_object: bool = False,
    ) -> str:
        """Call the wrapped provider, tolerating one that predates the flags (continuation stays ON, JSON mode dropped when the signature does not name it)."""
        flags: dict[str, bool] = {}
        if not allow_continuation:
            flags["allow_continuation"] = False
        if json_object:
            flags["json_object"] = True
        flags = accepted_flags(self._llm.complete, flags)
        return self._llm.complete(
            system=system, user=user,
            max_tokens=max_tokens, temperature=temperature,
            **flags,
        )

    def stream_complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        on_token: Any | None = None,
    ) -> str:
        """Route one STREAMED completion through the same billing as `complete`; no provider failover (chunks may already be on screen, replay would emit the answer twice): the error is recorded and re-raised. A provider without `stream_complete` falls back to the billed `complete`."""
        # Same health check as `complete` but strictly PRE-stream, so it does not conflict with the no-mid-stream-failover rule.
        _pre_provider = str(getattr(self._llm, "provider", self.route.provider) or "")
        _unhealthy = self._provider_unhealthy(_pre_provider)
        if _unhealthy:
            _replacement = self._next_provider_llm([_pre_provider])
            if _replacement is not None:
                self._adopt_failover(_replacement)
                self.route = replace(
                    self.route,
                    reason=(
                        f"provider_unhealthy:{_pre_provider}:{_unhealthy}"
                        f"->{self.provider}"
                    ),
                )
        raw_stream = getattr(self._llm, "stream_complete", None)
        if raw_stream is None:
            return self.complete(
                system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
            )
        provider = str(getattr(self._llm, "provider", self.route.provider) or "")
        model = str(getattr(self._llm, "model", self.route.model) or "")
        self.ledger.assert_can_start(
            role=self.role,
            provider=provider,
            model=model,
            system=system,
            user=user,
            max_output_tokens=max_tokens,
            cost_tier=self.cost_tier,
        )
        self.ledger.log_start(
            role=self.role,
            provider=provider,
            model=model,
            route_reason=self.route.reason,
            cost_tier=self.cost_tier,
        )
        started_at = utc_now_iso()
        started = time.perf_counter()
        try:
            output = raw_stream(
                system=system, user=user,
                max_tokens=max_tokens, temperature=temperature,
                on_token=on_token,
            )
        except Exception as exc:
            self.ledger.record(
                role=self.role,
                provider=provider,
                model=model,
                route_reason=self.route.reason,
                cost_tier=self.cost_tier,
                status="error",
                input_tokens=0,
                output_tokens=0,
                estimated=True,
                started_at=started_at,
                completed_at=utc_now_iso(),
                duration_ms=int((time.perf_counter() - started) * 1000),
                error=f"{type(exc).__name__}: {exc}",
            )
            raise
        input_tokens, output_tokens, estimated = usage_from_llm_or_estimate(
            self._llm,
            system=system,
            user=user,
            output=output,
        )
        cache_hit_tokens, cache_miss_tokens = cache_from_llm(self._llm)
        self.ledger.record(
            role=self.role,
            provider=provider,
            model=model,
            route_reason=self.route.reason,
            cost_tier=self.cost_tier,
            status="success",
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            estimated=estimated,
            started_at=started_at,
            completed_at=utc_now_iso(),
            duration_ms=int((time.perf_counter() - started) * 1000),
            cache_hit_tokens=cache_hit_tokens,
            cache_miss_tokens=cache_miss_tokens,
        )
        return output

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
        *,
        allow_continuation: bool = True,
        json_object: bool = False,
    ) -> str:
        """Route one completion (see core.llm.LLM.complete); ``allow_continuation=False`` goes to the provider wrapper for answers that must parse as a whole."""
        route_reason = self.route.reason
        tried: list[str] = []
        while True:
            provider = str(getattr(self._llm, "provider", self.route.provider) or "")
            model = str(getattr(self._llm, "model", self.route.model) or "")
            # Consult the ledger BEFORE the first call so an empty balance is remembered across processes; first iteration only, after a failover re-checking would loop.
            if not tried:
                unhealthy = self._provider_unhealthy(provider)
                if unhealthy:
                    tried.append(provider)
                    replacement = self._next_provider_llm(tried)
                    if replacement is not None:
                        self._adopt_failover(replacement)
                        route_reason = (
                            f"provider_unhealthy:{provider}:{unhealthy}"
                            f"->{self.provider}"
                        )
                        choice = getattr(self, "_failover_choice_reason", "")
                        if choice:
                            route_reason += f"|{choice}"
                        continue
                    # No alternative found: proceed with the configured provider; refusing to work is worse than one probe call.
            self.ledger.assert_can_start(
                role=self.role,
                provider=provider,
                model=model,
                system=system,
                user=user,
                max_output_tokens=max_tokens,
                cost_tier=self.cost_tier,
            )
            self.ledger.log_start(
                role=self.role,
                provider=provider,
                model=model,
                route_reason=route_reason,
                cost_tier=self.cost_tier,
            )
            started_at = utc_now_iso()
            started = time.perf_counter()
            try:
                output = self._call_llm(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
                    allow_continuation=allow_continuation,
                    json_object=json_object,
                )
            except Exception as exc:
                completed_at = utc_now_iso()
                self.ledger.record(
                    role=self.role,
                    provider=provider,
                    model=model,
                    route_reason=route_reason,
                    cost_tier=self.cost_tier,
                    status="error",
                    input_tokens=0,
                    output_tokens=0,
                    estimated=True,
                    started_at=started_at,
                    completed_at=completed_at,
                    duration_ms=int((time.perf_counter() - started) * 1000),
                    error=f"{type(exc).__name__}: {exc}",
                )
                tried.append(provider)
                replacement = self._failover_llm(exc, tried)
                if replacement is not None:
                    # The substitute is a different model on a different provider; its price is not the original route's.
                    self._adopt_failover(replacement)
                    route_reason = f"provider_failover:{provider}->{self.provider}"
                    choice = getattr(self, "_failover_choice_reason", "")
                    if choice:
                        route_reason += f"|{choice}"
                    continue
                raise
            completed_at = utc_now_iso()
            input_tokens, output_tokens, estimated = usage_from_llm_or_estimate(
                self._llm,
                system=system,
                user=user,
                output=output,
            )
            cache_hit_tokens, cache_miss_tokens = cache_from_llm(self._llm)
            self.ledger.record(
                role=self.role,
                provider=provider,
                model=model,
                route_reason=route_reason,
                cost_tier=self.cost_tier,
                status="success",
                input_tokens=input_tokens,
                output_tokens=output_tokens,
                estimated=estimated,
                started_at=started_at,
                completed_at=completed_at,
                duration_ms=int((time.perf_counter() - started) * 1000),
                cache_hit_tokens=cache_hit_tokens,
                cache_miss_tokens=cache_miss_tokens,
            )
            return output


def _clean_env(name: str) -> str | None:
    value = os.getenv(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def _env_bool(name: str) -> bool:
    value = _clean_env(name)
    if value is None:
        return False
    return value.lower() in {"1", "true", "yes", "y", "on"}


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


# Preference order for auto-selecting a credentialed provider when the routed one cannot authenticate; `mock` is excluded (only with AGENT_ALLOW_MOCK_ROUTING). `local` is last (a last resort, only with both its variables set); `deepseek` is second: the cheap rescuer right after the default brain, ahead of unfunded providers.
_PROVIDER_FALLBACK_ORDER: tuple[str, ...] = (
    "openai", "deepseek", "anthropic", "huggingface", "local",
)


def _first_credentialed_provider() -> str | None:
    """First real provider (by preference order) whose API key is present."""
    for provider in _PROVIDER_FALLBACK_ORDER:
        if _provider_has_credentials(provider):
            return provider
    return None


# Provider/key failover: when a routed provider's key runs out of money, hits a rate limit or is rejected mid-task, switch to the next credentialed provider and retry; distinct from `_llm_factory`, which heals before the first call.

# HTTP statuses meaning this key cannot serve the request but another might: 401, 402, 403, 429.
_SWITCH_KEY_STATUS: frozenset[int] = frozenset({401, 402, 403, 429})

# Lower-cased exception class-name fragments meaning out of quota / not authorised; duck-typed to avoid importing provider SDKs.
_SWITCH_KEY_NAME_MARKERS: tuple[str, ...] = (
    "ratelimit",
    "authentication",
    "permissiondenied",
    "insufficientquota",
)

# Lower-cased error-message fragments for a switch-key condition, two vocabularies: the DURABLE class (empty balance, bad key; `_KEY_CLASS_TEXT_MARKERS`) fails over per call and demotes provider health; the TRANSIENT class (rate limits) fails over but never demotes. The bare substring "quota" in the durable list deliberately absorbs the quota markers (`insufficient_quota` means an exhausted account); narrowing it would bring back repeated empty-balance retries.
_TRANSIENT_SWITCH_TEXT_MARKERS: tuple[str, ...] = (
    "rate limit",
    "rate_limit",
    "ratelimit",
)
_SWITCH_KEY_TEXT_MARKERS: tuple[str, ...] = (
    _TRANSIENT_SWITCH_TEXT_MARKERS + _KEY_CLASS_TEXT_MARKERS
)


def _is_switch_key_error(exc: BaseException) -> bool:
    """True when *exc* means this key cannot pay for or serve the request, so another provider might succeed; deliberately conservative (content-policy, validation, network/timeout errors do not match)."""
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    try:
        if isinstance(status, int) and status in _SWITCH_KEY_STATUS:
            return True
    except Exception:  # noqa: BLE001, S110 — pragma: no cover - defensive
        pass
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _SWITCH_KEY_NAME_MARKERS):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _SWITCH_KEY_TEXT_MARKERS)


# Exception *class name* fragments for local server downtime / hang.
_LOCAL_UNAVAILABLE_NAME_MARKERS: tuple[str, ...] = (
    "apiconnectionerror",
    "apitimeouterror",
    "connecterror",
    "connecttimeout",
    "readtimeout",
    "timeoutexception",
)

# Error *message* fragments for local downtime (connection refused, hang, etc.).
_LOCAL_UNAVAILABLE_TEXT_MARKERS: tuple[str, ...] = (
    "connection refused",
    "connection reset",
    "connect error",
    "connecterror",
    "failed to connect",
    "timed out",
    "timeout",
    "actively refused",
    "name or service not known",
    "nodename nor servname",
    "server disconnected",
    "remote end closed",
)


def _is_local_unavailable_error(exc: BaseException) -> bool:
    """True when *exc* means the local OpenAI-compatible server is down, unreachable or timed out; used only when the current provider is ``local``."""
    if isinstance(exc, (TimeoutError, ConnectionError)):
        return True
    errno_val = getattr(exc, "errno", None)
    if isinstance(exc, OSError) and errno_val in {
        errno.ECONNREFUSED,
        errno.ECONNRESET,
        errno.ETIMEDOUT,
        errno.ENETUNREACH,
        errno.EHOSTUNREACH,
    }:
        return True
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _LOCAL_UNAVAILABLE_NAME_MARKERS):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _LOCAL_UNAVAILABLE_TEXT_MARKERS)


def _next_failover_provider(tried: Sequence[str]) -> str | None:
    """Next credentialed provider (by preference order) not already attempted."""
    tried_lower = {str(t).strip().lower() for t in tried}
    for provider in _PROVIDER_FALLBACK_ORDER:
        if provider in tried_lower:
            continue
        if _provider_has_credentials(provider):
            return provider
    return None


def _provider_failover_enabled() -> bool:
    """Whether live provider/key failover is active (default ON; AGENT_PROVIDER_FAILOVER=0/false/no/off disables)."""
    value = _clean_env("AGENT_PROVIDER_FAILOVER")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _default_provider_name(provider: str | None) -> str:
    """Resolve the provider that ``llm.LLM`` would use (mirrors ``llm._provider()``: AGENT_PROVIDER, then anthropic)."""
    resolved = _normalise_provider(provider)
    if resolved:
        return resolved
    env = (os.getenv("AGENT_PROVIDER", "") or "").strip().lower()
    return env or "anthropic"


def _llm_factory(provider: str | None, model: str | None) -> LLM:
    """Build an LLM client, healing an un-credentialed provider choice."""
    resolved = _default_provider_name(provider)
    known_real_provider = resolved in _DEFAULT_PROVIDER_ENV and resolved != "mock"
    if not known_real_provider or _provider_has_credentials(resolved):
        return LLM(provider=provider, model=model)

    substitute = _first_credentialed_provider()
    if substitute is not None:
        # Switch provider and drop the model so the substitute's own default is used.
        return LLM(provider=substitute, model=None)
    if _env_bool("AGENT_ALLOW_MOCK_ROUTING"):
        return LLM(provider="mock", model="mock-1")
    raise RuntimeError(
        f"No API credentials found for model provider '{resolved}'. "
        "Set one of OPENAI_API_KEY, ANTHROPIC_API_KEY or HF_TOKEN "
        "(and optionally AGENT_PROVIDER to choose which), or set "
        "AGENT_ALLOW_MOCK_ROUTING=1 to run the offline mock model."
    )


def _builtin_model_specs() -> tuple[ModelSpec, ...]:
    return (
        ModelSpec(
            id="anthropic-default",
            provider="anthropic",
            model="claude-sonnet-4-5",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="high",
            cost_tier="medium",
            requires_env=_DEFAULT_PROVIDER_ENV["anthropic"],
            source="builtin",
            notes="default reasoning/coding route",
        ),
        ModelSpec(
            id="openai-default-small",
            provider="openai",
            model="gpt-4o-mini",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="standard",
            cost_tier="low",
            requires_env=_DEFAULT_PROVIDER_ENV["openai"],
            source="builtin",
            notes="cheap general fallback when OpenAI is the available provider",
        ),
        ModelSpec(
            id="hf-default",
            provider="huggingface",
            model="meta-llama/Llama-3.3-70B-Instruct",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="standard",
            cost_tier="low",
            requires_env=_DEFAULT_PROVIDER_ENV["huggingface"],
            source="builtin",
            notes="HuggingFace router-compatible fallback model",
        ),
        ModelSpec(
            id="mock",
            provider="mock",
            model="mock-1",
            roles=tuple(role.value for role in ModelRole),
            quality_tier="cheap",
            cost_tier="free",
            requires_env=_DEFAULT_PROVIDER_ENV["mock"],
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
    try:
        return _model_specs_from_json_data(
            data,
            source="env:AGENT_MODEL_REGISTRY_JSON",
        )
    except ValueError as exc:
        raise ValueError(f"AGENT_MODEL_REGISTRY_JSON: {exc}") from exc


def _custom_model_specs_from_path() -> tuple[ModelSpec, ...]:
    raw_path = _clean_env("AGENT_MODEL_REGISTRY_PATH")
    explicit = raw_path is not None
    path = Path(raw_path) if raw_path else Path("config") / "model_registry.json"
    if not path.exists():
        if explicit:
            raise ValueError(f"AGENT_MODEL_REGISTRY_PATH does not exist: {path}")
        return ()
    if not path.is_file():
        raise ValueError(f"model registry path is not a file: {path}")
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"model registry file is not valid JSON: {path}: {exc}") from exc
    return _model_specs_from_json_data(
        data,
        source=f"file:{path}",
    )


class ModelRegistryShapeError(TypeError, ValueError):
    """A registry entry of the wrong type; also a ValueError, because the loader catches ValueError and falls back to the defaults."""


def _model_specs_from_json_data(data: Any, *, source: str) -> tuple[ModelSpec, ...]:
    items = data.get("models", []) if isinstance(data, dict) else data
    if not isinstance(items, list):
        raise ModelRegistryShapeError("model registry must be a list or {'models': [...]}")
    specs: list[ModelSpec] = []
    for idx, item in enumerate(items):
        if not isinstance(item, dict):
            raise ModelRegistryShapeError(f"model registry item {idx} must be an object")
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
            raise ModelRegistryShapeError(f"model registry item {idx} roles must be string or list")
        raw_requires = item.get("requires_env")
        if raw_requires is None:
            requires_env = _DEFAULT_PROVIDER_ENV.get(provider, ())
        elif isinstance(raw_requires, str):
            requires_env = tuple(r.strip() for r in raw_requires.split(",") if r.strip())
        elif isinstance(raw_requires, list):
            requires_env = tuple(str(r).strip() for r in raw_requires if str(r).strip())
        else:
            raise ValueError(
                f"model registry item {idx} requires_env must be string or list"
            )
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
                requires_env=requires_env,
                source=source,
                notes=str(item.get("notes") or ""),
            )
        )
    return tuple(specs)


def _within_cost_limit(spec: ModelSpec, max_cost_tier: str | None) -> bool:
    if max_cost_tier is None:
        return True
    spec_rank = _COST_RANK.get(spec.cost_tier, _COST_RANK["unknown"])
    max_rank = _COST_RANK.get(max_cost_tier, _COST_RANK["unknown"])
    return spec_rank <= max_rank


def _model_score_for_policy(
    spec: ModelSpec,
    role_key: str,
    policy_name: str,
) -> tuple[int, int, int, int, int, str]:
    exact_role = 1 if role_key in spec.roles else 0
    quality = _QUALITY_SCORE.get(spec.quality_tier, _QUALITY_SCORE["unknown"])
    cheapness = _COST_SCORE.get(spec.cost_tier, _COST_SCORE["unknown"])
    context = spec.context_window or 0
    custom = 1 if spec.source != "builtin" else 0

    if policy_name == "cost":
        return (exact_role, cheapness, quality, custom, context, spec.id)
    if policy_name == "quality":
        return (exact_role, quality, context, custom, cheapness, spec.id)
    if policy_name == "offline":
        mock = 1 if spec.provider == "mock" else 0
        return (mock, exact_role, cheapness, quality, context, spec.id)
    if role_key in {ModelRole.PLANNER.value, ModelRole.REPAIR_PROPOSAL.value}:
        return (exact_role, quality, context, custom, cheapness, spec.id)
    if role_key in {ModelRole.MEMORY_SUMMARY.value, ModelRole.VERIFIER.value}:
        return (exact_role, cheapness, quality, custom, context, spec.id)
    return (exact_role, quality, cheapness, custom, context, spec.id)


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
        selection_policy: ModelSelectionPolicy | None = None,
        usage_ledger: ModelUsageLedger | None = None,
        routing_policy: Any | None = None,
    ):
        # Operator routing policy: read first in `route_for`, ahead of the env pins (core/model_routing_policy.py).
        self.routing_policy = routing_policy
        self._policy_stamp: tuple[int, int] | None = None
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
        self._tracked_cache: dict[str, Any] = {}
        self.registry = registry or ModelRegistry()
        self.selection_policy = selection_policy or ModelSelectionPolicy()
        self.usage_ledger = usage_ledger

    @classmethod
    def single(cls, llm: Any) -> ModelRouter:
        """Compatibility mode: every role returns the same LLM object."""

        return cls(static_llm=llm)

    @classmethod
    def from_env(
        cls,
        *,
        llm_factory: LLMFactory | None = None,
        usage_ledger: ModelUsageLedger | None = None,
        routing_policy: Any | None = None,
    ) -> ModelRouter:
        """Build a router from default (AGENT_PROVIDER / AGENT_MODEL) and role-specific (AGENT_<ROLE>_PROVIDER / _MODEL) environment variables for planner, synthesizer, repair, repair_proposal, memory, verifier."""

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
            selection_policy=ModelSelectionPolicy.from_env(),
            usage_ledger=usage_ledger,
            routing_policy=routing_policy,
        )

    def route_for(self, role: ModelRole | str) -> ModelRoute:
        role_key = _coerce_role(role)
        agent_route = agent_policy_route(self.routing_policy, role_key)  # operator policy first
        if agent_route is not None:
            return agent_route
        route = self._routes.get(role_key)
        if route is not None:
            return route
        registry_spec = self.registry.best_for_role(
            role_key,
            policy=self.selection_policy,
        )
        if registry_spec is not None:
            return ModelRoute(
                role=role_key,
                provider=registry_spec.provider,
                model=registry_spec.model,
                reason=f"policy:{self.selection_policy.name}:{registry_spec.id}",
            )
        # Nothing is configured for this role, so the default is served either way; but "no configuration" and "no such role" differ, and a misspelled role (a free string on contracts) is named in the route reason, which already reaches the usage ledger.
        known_role = role_key in KNOWN_MODEL_ROLES
        return ModelRoute(
            role=role_key,
            provider=self.default_provider,
            model=self.default_model,
            reason="default" if known_role else "default:unknown_role",
        )

    def for_role(self, role: ModelRole | str) -> Any:
        """Return the LLM-like object for `role`, creating it once."""

        if self._static_llm is not None:
            return self._static_llm
        role_key = _coerce_role(role)
        self._policy_stamp = drop_clients_if_policy_moved(
            self.routing_policy, self._policy_stamp, self._tracked_cache,
        )
        if self.usage_ledger is not None and role_key in self._tracked_cache:
            return self._tracked_cache[role_key]
        route = self.route_for(role_key)
        provider = route.provider or self.default_provider
        model = route.model or self.default_model
        provider, model, capped_reason = self._cap_role_route(
            role_key, provider, model, route.reason
        )
        if capped_reason != route.reason:
            route = ModelRoute(
                role=route.role,
                provider=provider,
                model=model,
                reason=capped_reason,
            )
        cache_key = (provider, model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, model)
        llm = self._cache[cache_key]
        if self.usage_ledger is None:
            return llm
        route = self._route_with_resolved_identity(route, llm, provider, model)
        tracked = UsageTrackedLLM(
            llm,
            role=role_key,
            route=route,
            cost_tier=self._cost_tier_for_route(route),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
            reprice=self._reprice_for_failover,
        )
        self._tracked_cache[role_key] = tracked
        return tracked

    def _route_with_resolved_identity(
        self,
        route: ModelRoute,
        llm: Any,
        provider: str | None,
        model: str | None,
    ) -> ModelRoute:
        """Return *route* with the provider/model the call will really use; an explicit route wins, the client fills only the blanks."""

        resolved_provider = (
            (provider or "").strip() or (getattr(llm, "provider", "") or "").strip()
        )
        resolved_model = (
            (model or "").strip() or (getattr(llm, "model", "") or "").strip()
        )
        if resolved_provider == (route.provider or "") and resolved_model == (
            route.model or ""
        ):
            return route
        return ModelRoute(
            role=route.role,
            provider=resolved_provider or None,
            model=resolved_model or None,
            reason=route.reason,
        )

    def _for_role_with_reason(self, role_key: str, route_reason: str) -> Any:
        """Return the standard role LLM with a custom ``route_reason`` (e.g. ``deep_downgraded:missing_reason``); not cached, so the one-off reason never poisons a later `for_role`."""
        if self._static_llm is not None:
            return self._static_llm
        route = self.route_for(role_key)
        provider = route.provider or self.default_provider
        model = route.model or self.default_model
        # keep `agent_policy:<id>` first, tier note after
        if str(route.reason or "").startswith("agent_policy:"):
            route_reason = f"{route.reason}|{route_reason}"
        provider, model, route_reason = self._cap_role_route(
            role_key, provider, model, route_reason
        )
        cache_key = (provider, model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, model)
        llm = self._cache[cache_key]
        if self.usage_ledger is None:
            return llm
        stamped = self._route_with_resolved_identity(
            ModelRoute(
                role=role_key,
                provider=provider,
                model=model,
                reason=route_reason,
            ),
            llm,
            provider,
            model,
        )
        return UsageTrackedLLM(
            llm,
            role=role_key,
            route=stamped,
            cost_tier=self._cost_tier_for_route(stamped),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
            reprice=self._reprice_for_failover,
        )

    def _declared_model_for(self, provider: str) -> str:
        """Concrete model the operator has declared for *provider* (config/model_registry.json, or LOCAL_LLM_MODEL for ``local``) when model_catalog cannot describe it; returns "" when nothing was declared."""
        # Operator specs before builtins, affordable before capped by the ceiling: a builtin would shadow the config, and without a price the frontier model would come first.
        specs = [
            s for s in self.registry.list()
            if _normalise_provider(s.provider) == provider and s.model
        ]
        specs.sort(key=lambda s: (
            not self._tier_model_within_cost_limit(provider, s.model),
            s.source == "builtin",
        ))
        if specs:
            return specs[0].model
        from core.llm import _default_model

        return (_default_model(provider) or "").strip()

    def _resolve_tier_provider(
        self, tier: Any, role_key: str
    ) -> tuple[str | None, str | None, list[str]]:
        """Choose a provider for *tier* by complexity preference; returns (provider, reason, skipped) or (None, None, skipped) to keep the role default. The model is decided by the caller via tier_model_for. An explicit per-role provider, or one named in AGENT_TIER_PROVIDERS_* with a declared model, always wins."""
        explicit = self._routes.get(role_key)
        if explicit is not None and _normalise_provider(explicit.provider):
            return None, None, []

        from core.model_catalog import tier_model_for

        tier_value = tier.value
        operator_named = _tier_providers_are_explicit(tier_value)
        skipped: list[str] = []
        for prov in _tier_provider_prefs(tier_value):
            norm = _normalise_provider(prov)
            if not norm:
                continue
            if norm not in SUPPORTED_PROVIDERS or not _provider_has_credentials(norm):
                # Unsupported provider or missing credentials: skip.
                skipped.append(f"provider_unavailable:{norm}")
                continue
            tier_model = tier_model_for(tier, norm)
            model_source = "catalog"
            if not tier_model and operator_named:
                tier_model = self._declared_model_for(norm)
                model_source = "declared"
            if not tier_model:
                skipped.append(f"no_model:{norm}")
                continue
            if not self._tier_model_within_cost_limit(norm, tier_model):
                # The operator capped spend and this provider's model is above the cap: try the next preference.
                skipped.append(f"cost_limit:{norm}")
                continue
            reason = f"complexity:{tier_value}:{norm}"
            if model_source == "declared":
                # A spare is acknowledged in the journal, otherwise it is indistinguishable from the catalog model and staleness is invisible.
                reason += "|model_source:declared"
            return norm, reason, skipped
        return None, None, skipped

    def _tier_model_within_cost_limit(self, provider: str, model: str) -> bool:
        """Whether *model* respects ``AGENT_MODEL_MAX_COST``."""

        limit = self.selection_policy.max_cost_tier
        if limit is None:
            return True
        route = ModelRoute(role="", provider=provider, model=model, reason="")
        cost_tier = self._cost_tier_for_route(route)
        return _COST_RANK.get(cost_tier, _COST_RANK["unknown"]) <= _COST_RANK.get(
            limit, _COST_RANK["unknown"]
        )

    def _cap_role_route(
        self, role_key: str, provider: str | None, model: str | None, reason: str
    ) -> tuple[str | None, str | None, str]:
        """Apply ``AGENT_MODEL_MAX_COST`` to a role route."""

        limit = self.selection_policy.max_cost_tier
        if limit is None:
            return provider, model, reason
        norm = _normalise_provider(provider) or (provider or "")
        if not model or self._tier_model_within_cost_limit(norm, model):
            return provider, model, reason

        spec = self.registry.best_for_role(
            role_key,
            policy=ModelSelectionPolicy(
                name="cost",
                max_cost_tier=limit,
                allow_mock=self.selection_policy.allow_mock,
                require_available=self.selection_policy.require_available,
            ),
        )
        if spec is not None and self._tier_model_within_cost_limit(
            _normalise_provider(spec.provider) or spec.provider, spec.model
        ):
            return (
                spec.provider,
                spec.model,
                f"{reason}|cost_limit:{norm}:{model}->{spec.id}",
            )

        # Nothing affordable is configured for this role: proceed (refusing would strand the agent) but record in the ledger that the ceiling was exceeded.
        return provider, model, f"{reason}|cost_limit_exceeded:{norm}:{model}"

    def for_task(
        self,
        role: ModelRole | str,
        task: str,
        *,
        escalation: Any = None,
        force_tier: Any = None,
        task_role: str | None = None,
    ) -> Any:
        """Like `for_role` but selects the model by task complexity (env AGENT_MODEL_TIER_*, then config/model_catalog.json, then `for_role`). `escalation`: operator-supplied OperatorEscalation; without a valid reason DEEP downgrades to standard (the agent cannot open Opus for itself). `force_tier` pins the tier but a forced DEEP still passes the gate. `task_role` (from RoleRouter) lets assess_complexity refuse LIGHT for code-editing roles; it never opens a stronger tier."""
        from core.model_catalog import tier_model_for
        from core.task_complexity import ComplexityTier, assess_complexity

        if self._static_llm is not None:
            return self._static_llm

        role_key = _coerce_role(role)
        if force_tier is not None:
            tier = force_tier if isinstance(force_tier, ComplexityTier) else ComplexityTier(str(force_tier))
        else:
            tier = assess_complexity(task, role=role_key, task_role=task_role)

        # TD-010: pick the PROVIDER by complexity tier among supported providers (LIGHT->huggingface/openai, STANDARD->openai, DEEP->anthropic; AGENT_TIER_PROVIDERS_*); None keeps the role-default behaviour.
        route = self.route_for(role_key)
        pref_provider, pref_reason, skipped = self._resolve_tier_provider(tier, role_key)

        if pref_provider is not None:
            # A preferred provider won: the model still comes from the catalog unless the operator named the provider and the catalog has no fetcher for it.
            provider = pref_provider
            tier_model = tier_model_for(tier, provider)
            if not tier_model and _tier_providers_are_explicit(tier.value):
                tier_model = self._declared_model_for(provider)
            tier_reason = _append_skipped(pref_reason or f"complexity:{tier.value}:{provider}", skipped)
        elif tier == ComplexityTier.STANDARD:
            # STANDARD with no preferred provider reuses role routing; TD-021: stamp an honest reason so the ledger shows the tier and the fallback, not the opaque policy:* id.
            reason = _append_skipped(
                f"complexity:{tier.value}|fallback:role_default", skipped
            )
            return self._for_role_with_reason(role_key, reason)
        else:
            # LIGHT / DEEP fall back to the explicit/default role provider.
            provider = _normalise_provider(route.provider or self.default_provider) or "anthropic"
            tier_model = tier_model_for(tier, provider)
            # If the preference loop skipped providers this is a fallback to the role default, otherwise a plain complexity route.
            if skipped:
                tier_reason = _append_skipped("fallback:role_default", skipped)
            else:
                tier_reason = f"complexity:{tier.value}:{provider}"

            if tier == ComplexityTier.LIGHT and not tier_model:
                light_spec = self.registry.best_for_role(
                    role_key,
                    policy=ModelSelectionPolicy(
                        name="cost",
                        max_cost_tier="low",
                        allow_mock=self.selection_policy.allow_mock,
                        require_available=self.selection_policy.require_available,
                    ),
                )
                if light_spec is not None:
                    provider = light_spec.provider
                    tier_model = light_spec.model
                    tier_reason = _append_skipped(
                        f"complexity:{tier.value}:{light_spec.id}", skipped
                    )

        # Deep/Opus escalation gate: DEEP is the only expensive direction; without a valid operator reason it downgrades to standard and records WHY in route_reason.
        if tier == ComplexityTier.DEEP:
            from core.deep_escalation import (
                DeepEscalationRequest,
                evaluate_deep_escalation,
            )
            # A stale cache and a provider with no deep model give the same empty `tier_model` but need different operator actions, so the gate is told which; read only when the tier failed to resolve.
            _catalog_status = None
            if not tier_model:
                from core.model_catalog import catalog_freshness

                _catalog_status = catalog_freshness().get("status")

            decision = evaluate_deep_escalation(DeepEscalationRequest(
                role=role_key,
                reason=getattr(escalation, "reason", None),
                expected_output=getattr(escalation, "expected_output", None),
                deep_model_available=bool(tier_model),
                budget_ok=getattr(escalation, "budget_ok", False),
                operator_approved=getattr(escalation, "operator_approved", False),
                catalog_status=_catalog_status,
            ))
            # No extra event: the route_reason (e.g. `deep_downgraded:catalog_expired`) already reaches the usage ledger; a second channel would need syncing.
            if decision.effective_tier == "standard":
                # Serve the STANDARD tier like a native standard request, so AGENT_TIER_PROVIDERS_STANDARD applies too (it used to abandon the requested provider on hard questions).
                dg_provider, _dg_pref, dg_skipped = self._resolve_tier_provider(
                    ComplexityTier.STANDARD, role_key
                )
                dg_model = ""
                if dg_provider:
                    dg_model = tier_model_for(
                        ComplexityTier.STANDARD, dg_provider
                    ) or self._declared_model_for(dg_provider)
                if dg_provider and dg_model:
                    provider = dg_provider
                    tier_model = dg_model
                    tier_reason = _append_skipped(
                        decision.route_reason, [*skipped, *dg_skipped]
                    )
                else:
                    # No operator standard preference: unchanged behaviour, the role default.
                    return self._for_role_with_reason(
                        role_key, decision.route_reason
                    )
            else:
                tier_reason = decision.route_reason

        if not tier_model:
            # No model for this tier: fall back to role routing (TD-021: stamp the assessed tier and why it fell back).
            reason = _append_skipped(
                f"complexity:{tier.value}|fallback:role_default:no_tier_model",
                skipped,
            )
            return self._for_role_with_reason(role_key, reason)

        cache_key = (provider, tier_model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, tier_model)
        llm = self._cache[cache_key]

        if self.usage_ledger is None:
            return llm

        tier_route = ModelRoute(
            role=role_key,
            provider=provider,
            model=tier_model,
            reason=tier_reason,
        )
        return UsageTrackedLLM(
            llm,
            role=role_key,
            route=tier_route,
            # Price by the resolved model's real registry cost tier (free/low/medium/high/unknown), not the complexity tier name, which priced every call as unknown.
            cost_tier=self._cost_tier_for_route(tier_route),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
            reprice=self._reprice_for_failover,
        )

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
        payload = self.registry.to_payload()
        payload["selection_policy"] = self.selection_policy.to_dict()
        return payload

    def usage_snapshot(self) -> dict[str, Any] | None:
        if self.usage_ledger is None:
            return None
        return self.usage_ledger.snapshot()

    def _cost_tier_for_route(self, route: ModelRoute) -> str:
        provider = _normalise_provider(route.provider or self.default_provider)
        model = (route.model or self.default_model or "").strip()
        for spec in self.registry.list():
            if spec.provider == provider and spec.model == model:
                return spec.cost_tier
        return self._classified_cost_tier(model)

    def _reprice_for_failover(self, provider: str, model: str) -> str:
        """Cost tier for a provider/model pair a failover landed on; the original route's tier no longer describes what is paid for."""
        return self._cost_tier_for_route(
            ModelRoute(role="", provider=provider, model=model, reason="")
        )

    @staticmethod
    def _classified_cost_tier(model: str) -> str:
        """Cost band for a model nobody priced by hand."""

        name = (model or "").strip()
        if not name:
            return "unknown"

        from core.model_catalog import ComplexityTier, classify_model

        return {
            ComplexityTier.LIGHT: "low",
            ComplexityTier.STANDARD: "medium",
            ComplexityTier.DEEP: "high",
        }.get(classify_model(name), "unknown")
