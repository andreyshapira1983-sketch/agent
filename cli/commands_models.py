"""Model routing / registry / catalog / usage REPL commands.

Split out of ``main.py``. Each command works through ``agent.model_router`` and
``core`` helpers only — never back into ``main`` — so there is no import cycle.
``main.py`` re-exports every name below for the REPL dispatch.
"""
from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING

from cli.parsers import _split_meta_args
from core.model_registry_audit import audit_model_registry

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_models(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :models [--json]", file=sys.stderr)
        return True

    from core.model_catalog import catalog_summary
    payload = {
        "routes":   agent.model_router.routing_summary(),
        "registry": agent.model_router.registry_summary(),
        "catalog":  catalog_summary(),
    }
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== model routes ===", file=sys.stderr)
    for role, route in payload["routes"].items():
        print(
            f"  {role}: provider={route.get('provider')} "
            f"model={route.get('model')} reason={route.get('reason')}",
            file=sys.stderr,
        )
    policy = payload["registry"].get("selection_policy", {})
    print("=== model selection policy ===", file=sys.stderr)
    print(
        f"  name={policy.get('name')} max_cost={policy.get('max_cost_tier')} "
        f"allow_mock={policy.get('allow_mock')} "
        f"require_available={policy.get('require_available')}",
        file=sys.stderr,
    )
    print("=== model registry ===", file=sys.stderr)
    for spec in payload["registry"]["models"]:
        print(
            f"  {spec['id']} provider={spec['provider']} model={spec['model']} "
            f"roles={','.join(spec['roles'])} cost={spec['cost_tier']} "
            f"quality={spec['quality_tier']} supported={spec['provider_supported']} "
            f"available={spec['available']} source={spec['source']}",
            file=sys.stderr,
        )
    cat = payload["catalog"]
    if cat.get("status") == "no_catalog":
        print("=== model catalog === (not populated — run :refresh-models)", file=sys.stderr)
    else:
        print(f"=== model catalog (updated {cat.get('updated_at', '?')}) ===", file=sys.stderr)
        for provider, pdata in cat.get("providers", {}).items():
            tiers = pdata.get("tier_best", {})
            n = pdata.get("model_count", 0)
            tier_str = "  ".join(f"{t}={m}" for t, m in sorted(tiers.items()))
            print(f"  {provider} ({n} models): {tier_str}", file=sys.stderr)
    return True


def _handle_model_registry_audit(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :model-registry-audit [--json]", file=sys.stderr)
        return True
    audit = audit_model_registry(agent.model_router)
    agent.log.log("model_registry_audit", audit.to_dict())
    if as_json:
        print(json.dumps(audit.to_dict(), ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(audit.user_summary(), file=sys.stderr)
    return True


def _handle_refresh_models(rest: str, agent: AgentLoop) -> bool:
    """Query provider APIs, classify models into tiers, write config/model_catalog.json.

    Usage: :refresh-models [--anthropic] [--openai]

    The catalog is queried from the provider's own API (not web search).
    Results are classified by naming pattern (haiku→light, opus→deep, …)
    and saved to config/model_catalog.json. No model names are hardcoded.
    """
    from core.model_catalog import refresh_catalog
    from core.task_complexity import ComplexityTier

    tokens = _split_meta_args(rest)
    requested = [t.lstrip("-") for t in tokens if t.startswith("--")]
    providers = requested if requested else None  # None = all available

    print("\n(refresh-models: запрашиваю актуальный список моделей у провайдеров...)", file=sys.stderr)

    try:
        catalog = refresh_catalog(providers=providers)
    except Exception as exc:  # noqa: BLE001
        print(f"(refresh-models ошибка: {exc})", file=sys.stderr)
        print("  Проверьте ANTHROPIC_API_KEY / OPENAI_API_KEY в .env", file=sys.stderr)
        return True

    print("\n── Результат ────────────────────────────────────────────────────────", file=sys.stderr)
    for provider, pdata in catalog.get("providers", {}).items():
        print(f"\n  {provider.upper()}", file=sys.stderr)
        tier_best = pdata.get("tier_best", {})
        all_models = pdata.get("models", [])
        for tier in ComplexityTier:
            best = tier_best.get(tier.value, "(не найдено)")
            candidates = [m["id"] for m in all_models if m["tier"] == tier.value]
            print(f"    {tier.value:<8} → {best}", file=sys.stderr)
            if len(candidates) > 1:
                others = [m for m in candidates if m != best]
                print(f"             (другие: {', '.join(others[:4])})", file=sys.stderr)

    print("\n── Каталог сохранён → config/model_catalog.json ────────────────────", file=sys.stderr)
    print("  Агент автоматически использует эти модели при следующем запуске.", file=sys.stderr)
    print("  Для override задайте в .env:", file=sys.stderr)
    print("    AGENT_MODEL_TIER_LIGHT    = <model-id>", file=sys.stderr)
    print("    AGENT_MODEL_TIER_STANDARD = <model-id>", file=sys.stderr)
    print("    AGENT_MODEL_TIER_DEEP     = <model-id>", file=sys.stderr)
    return True


def _handle_model_usage(rest: str, agent: AgentLoop) -> bool:
    tokens = _split_meta_args(rest)
    as_json = "--json" in tokens
    if any(token != "--json" for token in tokens):
        print("Usage: :model-usage [--json]", file=sys.stderr)
        return True
    snapshot = agent.model_router.usage_snapshot()
    if snapshot is None:
        print("(model usage ledger is not enabled)", file=sys.stderr)
        return True
    if as_json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2), file=sys.stderr)
        return True
    totals = snapshot.get("totals", {})
    session_totals = snapshot.get("session_totals", {})
    print("=== model usage ===", file=sys.stderr)
    print(
        f"  history_calls={totals.get('calls', 0)} "
        f"history_tokens={totals.get('total_tokens', 0)} "
        f"history_cost_units={totals.get('cost_units', 0)}",
        file=sys.stderr,
    )
    print(
        f"  session_calls={session_totals.get('calls', 0)} "
        f"session_tokens={session_totals.get('total_tokens', 0)} "
        f"session_cost_units={session_totals.get('cost_units', 0)}",
        file=sys.stderr,
    )
    print("=== by role ===", file=sys.stderr)
    for role, data in snapshot.get("by_role", {}).items():
        print(
            f"  {role}: calls={data.get('calls', 0)} "
            f"tokens={data.get('total_tokens', 0)} "
            f"cost_units={data.get('cost_units', 0)}",
            file=sys.stderr,
        )
    print("=== by model ===", file=sys.stderr)
    for model, data in snapshot.get("by_model", {}).items():
        print(
            f"  {model}: calls={data.get('calls', 0)} "
            f"tokens={data.get('total_tokens', 0)} "
            f"cost_units={data.get('cost_units', 0)}",
            file=sys.stderr,
        )
    return True
