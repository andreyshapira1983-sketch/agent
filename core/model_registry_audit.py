"""Operator-facing audit for model registry and active routes.

The model router has two different surfaces:

* registry: all known candidates;
* routes: one selected model per runtime role.

This audit keeps that distinction visible so the operator can see why a small
number of active models may be selected from a larger catalog.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from core.model_router import ModelRouter


def _model_key(provider: str | None, model: str | None) -> str:
    return f"{provider or '-'}:{model or '-'}"


@dataclass(frozen=True)
class ModelRegistryAudit:
    route_count: int
    unique_route_models: tuple[str, ...]
    registry_model_count: int
    available_model_count: int
    custom_model_count: int
    unused_available_models: tuple[str, ...]
    unavailable_models: tuple[str, ...]
    unsupported_models: tuple[str, ...]
    selection_policy: dict[str, Any]
    warnings: tuple[str, ...]
    recommendations: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "catalog_mode": "local_config",
            "live_provider_catalog": False,
            "route_count": self.route_count,
            "unique_route_models": list(self.unique_route_models),
            "registry_model_count": self.registry_model_count,
            "available_model_count": self.available_model_count,
            "custom_model_count": self.custom_model_count,
            "unused_available_models": list(self.unused_available_models),
            "unavailable_models": list(self.unavailable_models),
            "unsupported_models": list(self.unsupported_models),
            "selection_policy": self.selection_policy,
            "warnings": list(self.warnings),
            "recommendations": list(self.recommendations),
        }

    def user_summary(self) -> str:
        lines = [
            "=== model registry audit ===",
            "catalog: local config, not live provider catalog",
            (
                f"routes: {self.route_count} roles -> "
                f"{len(self.unique_route_models)} unique active model(s)"
            ),
            (
                f"registry: {self.registry_model_count} known, "
                f"{self.available_model_count} available, "
                f"{self.custom_model_count} custom"
            ),
        ]
        policy_name = self.selection_policy.get("name")
        if policy_name:
            lines.append(
                "policy: "
                f"{policy_name} max_cost={self.selection_policy.get('max_cost_tier')} "
                f"require_available={self.selection_policy.get('require_available')}"
            )
        if self.unique_route_models:
            lines.append("active models:")
            lines.extend(f"  - {model}" for model in self.unique_route_models)
        if self.unused_available_models:
            lines.append("available but not selected:")
            lines.extend(f"  - {model}" for model in self.unused_available_models)
        if self.unavailable_models:
            lines.append("unavailable:")
            lines.extend(f"  - {model}" for model in self.unavailable_models)
        if self.unsupported_models:
            lines.append("unsupported provider:")
            lines.extend(f"  - {model}" for model in self.unsupported_models)
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {warning}" for warning in self.warnings)
        if self.recommendations:
            lines.append("recommendations:")
            lines.extend(f"  - {recommendation}" for recommendation in self.recommendations)
        return "\n".join(lines)


def audit_model_registry(router: ModelRouter) -> ModelRegistryAudit:
    routes = router.routing_summary()
    registry = router.registry_summary()
    specs = tuple(registry.get("models", ()))
    route_models = tuple(
        sorted(
            {
                _model_key(route.get("provider"), route.get("model"))
                for route in routes.values()
            }
        )
    )
    selected_keys = set(route_models)

    available_specs = [
        spec for spec in specs
        if spec.get("enabled", True)
        and spec.get("available")
        and spec.get("provider_supported")
    ]
    unavailable_specs = [
        spec for spec in specs
        if spec.get("enabled", True)
        and spec.get("provider_supported")
        and not spec.get("available")
    ]
    unsupported_specs = [
        spec for spec in specs
        if spec.get("enabled", True) and not spec.get("provider_supported")
    ]
    unused_available = [
        _format_spec(spec)
        for spec in available_specs
        if _model_key(spec.get("provider"), spec.get("model")) not in selected_keys
    ]

    warnings: list[str] = [
        "Model routes select one model per role; they do not display every registry candidate.",
        "Built-in model names are defaults, not an automatically refreshed provider catalog.",
    ]
    if len(route_models) < len(available_specs):
        warnings.append(
            "Some available models are not selected because the current policy picked stronger or cheaper role-specific routes."
        )
    if unavailable_specs:
        warnings.append(
            "Some registry models are unavailable because their required environment variables are missing."
        )
    if unsupported_specs:
        warnings.append(
            "Some registry models use providers that the local LLM adapter does not support yet."
        )

    custom_count = int(registry.get("custom_count", 0) or 0)
    recommendations: list[str] = []
    if custom_count == 0:
        recommendations.append(
            "Copy config/model_registry.example.json to config/model_registry.json and add new model IDs there."
        )
    recommendations.append(
        "Use :models to inspect active routes and :model-registry-audit to inspect candidates, availability and unused models."
    )
    recommendations.append(
        "Treat provider model discovery as an explicit operator update until a live provider catalog connector exists."
    )

    return ModelRegistryAudit(
        route_count=len(routes),
        unique_route_models=route_models,
        registry_model_count=len(specs),
        available_model_count=len(available_specs),
        custom_model_count=custom_count,
        unused_available_models=tuple(unused_available),
        unavailable_models=tuple(_format_spec(spec) for spec in unavailable_specs),
        unsupported_models=tuple(_format_spec(spec) for spec in unsupported_specs),
        selection_policy=dict(registry.get("selection_policy", {})),
        warnings=tuple(warnings),
        recommendations=tuple(recommendations),
    )


def _format_spec(spec: dict[str, Any]) -> str:
    return (
        f"{spec.get('id')} {spec.get('provider')}/{spec.get('model')} "
        f"roles={','.join(spec.get('roles') or [])} "
        f"cost={spec.get('cost_tier')} quality={spec.get('quality_tier')}"
    )
