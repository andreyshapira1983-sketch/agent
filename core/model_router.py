"""Role-based model routing.

The agent core should not be married to one model. This module keeps that
choice behind a small routing layer so planner, synthesis, repair proposal,
and memory summarisation can move independently as better models appear.
"""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Callable, Iterable, Mapping, Sequence

from core.llm import LLM
from core.model_usage import (
    ModelUsageLedger,
    usage_from_llm_or_estimate,
    utc_now_iso,
)


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
        if require_available and not self.requirements_satisfied():
            return False
        return True

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
    "huggingface": ("HF_TOKEN",),
    "mock": (),
}


# ── TD-010: provider-by-complexity preference ─────────────────────────────────
# Ordered provider preference per complexity tier, chosen only among providers
# that are already supported. NO local/Ollama backend and NO fake-local alias:
# a real local backend is a future extension point. Model names are never
# hardcoded here — the concrete model always comes from
# ``model_catalog.tier_model_for(tier, provider)``.
#
# Keyed by ComplexityTier.value so this module needs no task_complexity import.
_TIER_PROVIDER_PREF: dict[str, tuple[str, ...]] = {
    "light": ("huggingface", "openai"),
    "standard": ("openai",),
    "deep": ("anthropic",),
}

# Per-tier operator override. Comma-separated provider list, e.g.
#   AGENT_TIER_PROVIDERS_LIGHT="huggingface,openai"
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


def _provider_has_credentials(provider: str) -> bool:
    """True when every required env var for *provider* is set (non-empty).

    ``mock`` needs no credentials. Providers with no known env requirement are
    treated as unavailable so the preference loop skips them gracefully.
    """
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
    """How the router may choose from the model registry.

    `conservative` preserves the previous behaviour: explicit role env vars
    win, then custom registry entries, then the default provider/model. Other
    policies may select built-in registry models when the matching key exists.
    """

    name: str = "conservative"
    max_cost_tier: str | None = None
    allow_mock: bool = False
    require_available: bool = True

    @classmethod
    def from_env(cls) -> "ModelSelectionPolicy":
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
        if policy.name == "conservative":
            source_specs = self.custom_specs()
        else:
            source_specs = self._specs
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
    ):
        self._llm = llm
        self.role = role
        self.route = route
        self.cost_tier = cost_tier
        self.ledger = ledger
        # Factory used to rebuild the client on another provider during
        # failover. When ``None`` (e.g. no ledger / static LLM paths) failover
        # is disabled and behaviour is unchanged.
        self._llm_factory = llm_factory
        self.provider = getattr(llm, "provider", route.provider)
        self.model = getattr(llm, "model", route.model)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._llm, name)

    def _failover_llm(self, exc: BaseException, tried: Sequence[str]) -> Any | None:
        """Return a replacement LLM on another credentialed provider, or None.

        Only fires when failover is enabled, a factory is available, the error
        looks like a key/quota/auth problem, and another credentialed provider
        exists that hasn't been tried yet.
        """
        if self._llm_factory is None or not _provider_failover_enabled():
            return None
        if not _is_switch_key_error(exc):
            return None
        nxt = _next_failover_provider(tried)
        if nxt is None:
            return None
        try:
            # Drop the model so the substitute provider's own default is used
            # instead of a model name that belongs to the original provider.
            return self._llm_factory(nxt, None)
        except Exception:  # pragma: no cover - defensive
            return None

    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = 2048,
        temperature: float = 0.7,
    ) -> str:
        route_reason = self.route.reason
        tried: list[str] = []
        while True:
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
                route_reason=route_reason,
                cost_tier=self.cost_tier,
            )
            started_at = utc_now_iso()
            started = time.perf_counter()
            try:
                output = self._llm.complete(
                    system=system,
                    user=user,
                    max_tokens=max_tokens,
                    temperature=temperature,
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
                    self._llm = replacement
                    self.provider = getattr(replacement, "provider", None) or ""
                    self.model = getattr(replacement, "model", None) or ""
                    route_reason = f"provider_failover:{provider}->{self.provider}"
                    continue
                raise
            completed_at = utc_now_iso()
            input_tokens, output_tokens, estimated = usage_from_llm_or_estimate(
                self._llm,
                system=system,
                user=user,
                output=output,
            )
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


# Preference order for auto-selecting a credentialed provider when the routed
# provider cannot authenticate. ``mock`` is intentionally excluded — it is only
# used as a fallback when explicitly allowed via ``AGENT_ALLOW_MOCK_ROUTING``.
_PROVIDER_FALLBACK_ORDER: tuple[str, ...] = ("openai", "anthropic", "huggingface")


def _first_credentialed_provider() -> str | None:
    """First real provider (by preference order) whose API key is present."""
    for provider in _PROVIDER_FALLBACK_ORDER:
        if _provider_has_credentials(provider):
            return provider
    return None


# --- Provider/key failover ---------------------------------------------------
#
# When a routed provider's key runs out of money, hits its rate limit, or is
# rejected for auth reasons *mid-task*, we transparently switch to the next
# credentialed provider and retry the same call rather than crashing. This is
# distinct from ``_llm_factory`` (which heals an un-credentialed choice *before*
# the first call); failover reacts to live errors *during* a call.

# HTTP status codes that indicate the current key can't serve the request but a
# different key/provider might: unauthorized, payment required, forbidden,
# too-many-requests.
_SWITCH_KEY_STATUS: frozenset[int] = frozenset({401, 402, 403, 429})

# Exception *class name* fragments (lower-cased) raised by provider SDKs that
# mean "this key is out of quota / not authorised". Duck-typed so we never have
# to import the openai / anthropic SDKs here.
_SWITCH_KEY_NAME_MARKERS: tuple[str, ...] = (
    "ratelimit",
    "authentication",
    "permissiondenied",
    "insufficientquota",
)

# Error *message* fragments (lower-cased) that indicate a switch-key condition.
_SWITCH_KEY_TEXT_MARKERS: tuple[str, ...] = (
    "insufficient_quota",
    "insufficient quota",
    "exceeded your current quota",
    "rate limit",
    "rate_limit",
    "ratelimit",
    "quota",
    "invalid api key",
    "incorrect api key",
    "invalid_api_key",
    "authentication",
    "unauthorized",
    "permission denied",
    "billing",
    "payment required",
    "credit balance",
    "insufficient funds",
)


def _is_switch_key_error(exc: BaseException) -> bool:
    """True when *exc* looks like "this key can't pay for / isn't allowed to
    serve this request" — i.e. a *different* credentialed provider might succeed.

    Deliberately conservative: content-policy, validation and generic
    network/timeout errors do NOT match, so failover only fires for genuine
    key/quota/auth problems.
    """
    status = getattr(exc, "status_code", None)
    if status is None:
        status = getattr(exc, "status", None)
    try:
        if isinstance(status, int) and status in _SWITCH_KEY_STATUS:
            return True
    except Exception:  # pragma: no cover - defensive
        pass
    name = type(exc).__name__.lower()
    if any(marker in name for marker in _SWITCH_KEY_NAME_MARKERS):
        return True
    text = str(exc).lower()
    return any(marker in text for marker in _SWITCH_KEY_TEXT_MARKERS)


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
    """Whether live provider/key failover is active. Defaults to ON.

    Set ``AGENT_PROVIDER_FAILOVER=0`` (or false/no/off) to disable.
    """
    value = _clean_env("AGENT_PROVIDER_FAILOVER")
    if value is None:
        return True
    return value.lower() in {"1", "true", "yes", "y", "on"}


def _default_provider_name(provider: str | None) -> str:
    """Resolve the provider that ``llm.LLM`` would actually use.

    Mirrors ``llm._provider()``: an unset provider falls back to
    ``AGENT_PROVIDER`` and then to ``anthropic``.
    """
    resolved = _normalise_provider(provider)
    if resolved:
        return resolved
    env = (os.getenv("AGENT_PROVIDER", "") or "").strip().lower()
    return env or "anthropic"


def _llm_factory(provider: str | None, model: str | None) -> LLM:
    """Build an LLM client, healing an un-credentialed provider choice.

    The router can fall back to a real provider (historically hardcoded to
    ``anthropic``) even when no matching API key is set. Constructing that
    client and calling it later crashes deep inside the provider SDK with an
    opaque authentication ``TypeError``. Instead, when the resolved provider is
    a known real provider with no credentials we transparently switch to the
    first credentialed provider, or to ``mock`` when offline mock routing is
    allowed, or raise a clear, actionable error.

    ``mock``, already-credentialed providers and unknown providers are built
    unchanged (the latter still raise ``ValueError`` loudly in ``LLM``), so the
    hermetic mock-based test suite is unaffected.
    """
    resolved = _default_provider_name(provider)
    known_real_provider = resolved in _DEFAULT_PROVIDER_ENV and resolved != "mock"
    if not known_real_provider or _provider_has_credentials(resolved):
        return LLM(provider=provider, model=model)

    substitute = _first_credentialed_provider()
    if substitute is not None:
        # Switch provider; drop the model so the substitute's own default is
        # used instead of a model name that belongs to the original provider.
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


def _model_specs_from_json_data(data: Any, *, source: str) -> tuple[ModelSpec, ...]:
    if isinstance(data, dict):
        items = data.get("models", [])
    else:
        items = data
    if not isinstance(items, list):
        raise ValueError("model registry must be a list or {'models': [...]}")
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


def _model_score(spec: ModelSpec, role_key: str) -> tuple[int, int, int, int, str]:
    exact_role = 1 if role_key in spec.roles else 0
    quality = _QUALITY_SCORE.get(spec.quality_tier, _QUALITY_SCORE["unknown"])
    cost = _COST_SCORE.get(spec.cost_tier, _COST_SCORE["unknown"])
    context = spec.context_window or 0
    # Deterministic tie-breaker keeps repeated runs stable.
    return (exact_role, quality, cost, context, spec.id)


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
        self._tracked_cache: dict[str, Any] = {}
        self.registry = registry or ModelRegistry()
        self.selection_policy = selection_policy or ModelSelectionPolicy()
        self.usage_ledger = usage_ledger

    @classmethod
    def single(cls, llm: Any) -> "ModelRouter":
        """Compatibility mode: every role returns the same LLM object."""

        return cls(static_llm=llm)

    @classmethod
    def from_env(
        cls,
        *,
        llm_factory: LLMFactory | None = None,
        usage_ledger: ModelUsageLedger | None = None,
    ) -> "ModelRouter":
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
            selection_policy=ModelSelectionPolicy.from_env(),
            usage_ledger=usage_ledger,
        )

    def route_for(self, role: ModelRole | str) -> ModelRoute:
        role_key = _coerce_role(role)
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
        role_key = _coerce_role(role)
        if self.usage_ledger is not None and role_key in self._tracked_cache:
            return self._tracked_cache[role_key]
        route = self.route_for(role_key)
        provider = route.provider or self.default_provider
        model = route.model or self.default_model
        cache_key = (provider, model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, model)
        llm = self._cache[cache_key]
        if self.usage_ledger is None:
            return llm
        tracked = UsageTrackedLLM(
            llm,
            role=role_key,
            route=route,
            cost_tier=self._cost_tier_for_route(route),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
        )
        self._tracked_cache[role_key] = tracked
        return tracked

    def _for_role_with_reason(self, role_key: str, route_reason: str) -> Any:
        """Return the standard role LLM but stamp a custom ``route_reason``.

        Used by the deep-escalation gate to record a downgrade in the usage
        ledger (e.g. ``deep_downgraded:missing_reason``) while still serving the
        normal standard-tier model. Not stored in the role cache so the gate's
        one-off reason never poisons a later plain :meth:`for_role` call.
        """
        if self._static_llm is not None:
            return self._static_llm
        route = self.route_for(role_key)
        provider = route.provider or self.default_provider
        model = route.model or self.default_model
        cache_key = (provider, model)
        if cache_key not in self._cache:
            self._cache[cache_key] = self._llm_factory(provider, model)
        llm = self._cache[cache_key]
        if self.usage_ledger is None:
            return llm
        stamped = ModelRoute(
            role=role_key,
            provider=provider,
            model=model,
            reason=route_reason,
        )
        return UsageTrackedLLM(
            llm,
            role=role_key,
            route=stamped,
            cost_tier=self._cost_tier_for_route(route),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
        )

    def _resolve_tier_provider(
        self, tier: Any, role_key: str
    ) -> tuple[str | None, str | None, list[str]]:
        """TD-010: choose a provider for *tier* by complexity preference.

        Returns ``(provider, reason, skipped)`` when a supported, credentialed
        provider that also has a catalog/env tier model is found; otherwise
        ``(None, None, skipped)`` so the caller keeps today's role-default
        behavior. The concrete model is never decided here — only the provider;
        callers still resolve the model via ``tier_model_for(tier, provider)``.

        An explicit per-role provider (``AGENT_<ROLE>_PROVIDER``) disables the
        preference entirely so the operator's choice always wins.
        """
        explicit = self._routes.get(role_key)
        if explicit is not None and _normalise_provider(explicit.provider):
            return None, None, []

        from core.model_catalog import tier_model_for

        tier_value = tier.value
        skipped: list[str] = []
        for prov in _tier_provider_prefs(tier_value):
            norm = _normalise_provider(prov)
            if not norm:
                continue
            if norm not in SUPPORTED_PROVIDERS or not _provider_has_credentials(norm):
                # Unsupported (e.g. `local`) or missing credentials → skip.
                skipped.append(f"provider_unavailable:{norm}")
                continue
            if not tier_model_for(tier, norm):
                skipped.append(f"no_model:{norm}")
                continue
            return norm, f"complexity:{tier_value}:{norm}", skipped
        return None, None, skipped

    def for_task(
        self,
        role: ModelRole | str,
        task: str,
        *,
        escalation: Any = None,
        force_tier: Any = None,
    ) -> Any:
        """Like :meth:`for_role` but auto-selects model based on task complexity.

        Model selection order
        ─────────────────────
        1. env var  AGENT_MODEL_TIER_{LIGHT|STANDARD|DEEP}   (operator override)
        2. config/model_catalog.json                          (:refresh-models cache)
        3. :meth:`for_role`                                   (existing role routing)

        No model names are hardcoded. Model discovery is delegated to
        :mod:`core.model_catalog` which queries the provider API and
        classifies models by naming pattern (haiku→light, opus→deep, …).

        Env vars for explicit tier override (optional):
            AGENT_MODEL_TIER_LIGHT
            AGENT_MODEL_TIER_STANDARD
            AGENT_MODEL_TIER_DEEP

        ``escalation`` is an optional operator-supplied
        :class:`~core.deep_escalation.OperatorEscalation` (role-free). It only
        affects a DEEP request: without a valid operator reason the deep request
        gracefully downgrades to the standard tier (the agent can never open
        Opus for itself). LIGHT escalation is never gated.

        ``force_tier`` lets a caller pin the tier explicitly (e.g. the loop's
        cheap-path gate forcing LIGHT for a trivial no-tool turn) instead of
        deriving it from ``assess_complexity``. It can never open a more
        expensive tier without the normal escalation gate: a forced DEEP still
        passes through the operator-escalation check below.
        """
        from core.task_complexity import ComplexityTier, assess_complexity
        from core.model_catalog import tier_model_for

        if self._static_llm is not None:
            return self._static_llm

        role_key = _coerce_role(role)
        if force_tier is not None:
            tier = force_tier if isinstance(force_tier, ComplexityTier) else ComplexityTier(str(force_tier))
        else:
            tier = assess_complexity(task, role=role_key)

        # ── TD-010: provider-by-complexity preference ────────────────────────
        # Pick the PROVIDER by complexity tier among already-supported
        # providers (LIGHT→huggingface/openai, STANDARD→openai, DEEP→anthropic;
        # overridable via AGENT_TIER_PROVIDERS_*). Returns None when no
        # preferred provider is credentialed AND has a catalog tier model, in
        # which case we keep today's role-default behavior exactly.
        route = self.route_for(role_key)
        pref_provider, pref_reason, skipped = self._resolve_tier_provider(tier, role_key)

        if pref_provider is not None:
            # A preferred provider won — model still comes from the catalog.
            provider = pref_provider
            tier_model = tier_model_for(tier, provider)
            tier_reason = _append_skipped(pref_reason or f"complexity:{tier.value}:{provider}", skipped)
        elif tier == ComplexityTier.STANDARD:
            # STANDARD with no preferred provider → reuse normal role routing
            # (backward-compatible fast path; no catalog query, identity kept).
            # TD-021: keep the resolved model identical to for_role but stamp an
            # honest reason so the ledger shows the complexity tier and the
            # fallback, not the registry's opaque policy:* route id.
            reason = _append_skipped(
                f"complexity:{tier.value}|fallback:role_default", skipped
            )
            return self._for_role_with_reason(role_key, reason)
        else:
            # LIGHT / DEEP fall back to the explicit/default role provider.
            provider = _normalise_provider(route.provider or self.default_provider) or "anthropic"
            tier_model = tier_model_for(tier, provider)
            # If the preference loop actually skipped provider(s), this is a
            # fallback to the role default; otherwise it's a plain complexity
            # route to the (explicit or default) provider.
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

        # ── Deep/Opus escalation gate ────────────────────────────────────────
        # DEEP is the only expensive direction; LIGHT (cheap) is never gated.
        # Without a valid operator reason a deep request downgrades to the
        # standard tier, recording WHY in route_reason for the usage ledger.
        if tier == ComplexityTier.DEEP:
            from core.deep_escalation import (
                DeepEscalationRequest,
                evaluate_deep_escalation,
            )
            decision = evaluate_deep_escalation(DeepEscalationRequest(
                role=role_key,
                reason=getattr(escalation, "reason", None),
                expected_output=getattr(escalation, "expected_output", None),
                deep_model_available=bool(tier_model),
                budget_ok=getattr(escalation, "budget_ok", False),
                operator_approved=getattr(escalation, "operator_approved", False),
            ))
            if decision.effective_tier == "standard":
                return self._for_role_with_reason(role_key, decision.route_reason)
            tier_reason = decision.route_reason

        if not tier_model:
            # No model configured/discovered for this tier → fall back to role
            # routing. (DEEP with no model already downgraded above.)
            # TD-021: the resolved model is unchanged, but stamp an honest reason
            # so the ledger records the assessed tier and *why* it fell back
            # (no tier model) instead of the registry's opaque policy:* id.
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
            # Price by the resolved model's real registry cost tier, not the
            # complexity tier name — the usage ledger's cost table is keyed on
            # "free/low/medium/high/unknown", so passing "light/standard/deep"
            # here silently priced every complexity-routed call as "unknown".
            cost_tier=self._cost_tier_for_route(tier_route),
            ledger=self.usage_ledger,
            llm_factory=self._llm_factory,
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
        return "unknown"
