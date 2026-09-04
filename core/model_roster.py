"""The agent's eye on its own models: which providers exist, which have a key,
which roles they serve, whether they are healthy, what they cost, what was
spent today and how much of the day's ceiling is left.

Measured 2026-09-04 (operator's word «ставь глаз первым»): nothing in the
unattended context named a provider or a model; the only sensor the agent had
for its keys was a failure — a provider answering «no balance» three times and
the router moving on silently. Three keys were configured; he could not see
one. This module only SHOWS what the router, the catalog and the ledgers
already know. It never decides, never switches, never prints a key value.
"""
from __future__ import annotations

import os
from datetime import datetime, timezone
from typing import Any

#: Providers the roster reports on, in a stable order. Every key the
#: operator keeps in .env is listed, whether or not the code can call it:
#: the operator's complaint (2026-09-04) was «he has four keys and sees one»,
#: and a roster that listed only the router's providers would have repeated
#: that blindness. A key without a client is reported as such.
ROSTER_PROVIDERS: tuple[str, ...] = (
    "openai", "anthropic", "deepseek", "huggingface", "local", "google",
)

_KEY_ENV: dict[str, tuple[str, ...]] = {
    "openai": ("OPENAI_API_KEY",),
    "anthropic": ("ANTHROPIC_API_KEY",),
    "deepseek": ("DEEPSEEK_API_KEY",),
    "huggingface": ("HF_TOKEN",),
    "local": ("LOCAL_LLM_BASE_URL",),
    "google": ("GOOGLE_API_KEY", "GEMINI_API_KEY"),
}


def _key_present(provider: str, env: dict[str, str]) -> bool:
    """Case-insensitive on the name (the operator's .env spells one key
    `Google_API_KEY`; Windows reads it either way, a plain dict does not)."""
    wanted = {name.lower() for name in _KEY_ENV[provider]}
    return any((value or "").strip() for key, value in env.items() if key.lower() in wanted)


def _has_door(provider: str) -> bool:
    """Whether the router can build a client for this provider at all — read
    from the router's own credential table, not restated here."""
    try:
        from core.model_router import _DEFAULT_PROVIDER_ENV
    except Exception:  # noqa: BLE001 — no router table: no door can be claimed
        return False
    return provider in _DEFAULT_PROVIDER_ENV

#: Role prefixes as the router reads them (`core.model_router._ROLE_ENV_PREFIXES`).
_ROLE_PREFIXES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("planner", ("AGENT_PLANNER",)),
    ("synthesizer", ("AGENT_SYNTHESIZER", "AGENT_ANSWER")),
    ("repair", ("AGENT_REPAIR", "AGENT_REPAIR_PROPOSAL")),
    ("memory", ("AGENT_MEMORY", "AGENT_MEMORY_SUMMARY")),
    ("verifier", ("AGENT_VERIFIER",)),
)

_ERROR_CLASSES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("billing", ("balance", "billing", "credit", "insufficient")),
    ("quota", ("quota", "rate limit", "rate_limit", "429")),
    ("key", ("api key", "api_key", "unauthorized", "401", "invalid key", "authentication")),
)


def _roles_for(provider: str, env: dict[str, str]) -> tuple[str, ...]:
    default = (env.get("AGENT_PROVIDER") or "").strip().lower()
    roles: list[str] = []
    for role, prefixes in _ROLE_PREFIXES:
        chosen = ""
        for prefix in prefixes:
            value = (env.get(f"{prefix}_PROVIDER") or "").strip().lower()
            if value:
                chosen = value
                break
        if (chosen or default) == provider:
            roles.append(role)
    return tuple(roles)


def _error_class(text: str) -> str:
    low = (text or "").lower()
    for name, markers in _ERROR_CLASSES:
        if any(m in low for m in markers):
            return name
    return "other" if low else ""


def _today(now: datetime) -> str:
    return now.astimezone(timezone.utc).date().isoformat()


def _provider_rows(usage_ledger: Any, provider: str) -> list[dict]:
    reader = getattr(usage_ledger, "_recent_rows_for", None)
    if reader is None:
        return []
    try:
        return list(reader(provider, limit=400))
    except Exception:  # noqa: BLE001 — an unreadable ledger is «no rows», said so below
        return []


def model_roster(
    *,
    usage_ledger: Any = None,
    budget_ledger: Any = None,
    env: dict[str, str] | None = None,
    now: datetime | None = None,
) -> dict[str, Any]:
    """The roster as data. Every field is read from existing state; a missing
    source is reported as such, never as a healthy default."""
    env = dict(os.environ) if env is None else env
    moment = now or datetime.now(timezone.utc)
    today = _today(moment)
    providers: list[dict[str, Any]] = []
    for provider in ROSTER_PROVIDERS:
        rows = _provider_rows(usage_ledger, provider)
        today_rows = [r for r in rows if str(r.get("completed_at") or "").startswith(today)]
        last_error = ""
        for r in reversed(rows):
            if str(r.get("status") or "") != "success":
                last_error = str(r.get("error") or "")
                break
            break  # the newest row is a success: no standing error
        health = None
        health_known = False
        checker = getattr(usage_ledger, "provider_unhealthy", None)
        if checker is not None:
            try:
                health = checker(provider, now=moment)
                health_known = True
            except Exception:  # noqa: BLE001 — health unknown is a state, not «healthy»
                health_known = False
        tiers = {str(r.get("cost_tier") or "") for r in rows if r.get("cost_tier")}
        providers.append({
            "provider": provider,
            "key_present": _key_present(provider, env),
            "door": _has_door(provider),
            "roles": _roles_for(provider, env),
            "health": ("unknown" if not health_known else ("unhealthy" if health else "healthy")),
            "unhealthy_reason": health or "",
            "last_error_class": _error_class(last_error),
            "cost_tiers_seen": tuple(sorted(t for t in tiers if t)),
            "calls_today": len(today_rows),
            "cost_units_today": sum(int(r.get("cost_units") or 0) for r in today_rows),
            "tokens_today": sum(int(r.get("total_tokens") or 0) for r in today_rows),
        })
    day: dict[str, Any] = {"known": False}
    snapshot_fn = getattr(budget_ledger, "snapshot", None)
    if snapshot_fn is not None:
        try:
            snap = snapshot_fn(now=moment)
            for window in snap.get("windows", []):
                if window.get("name") == "day":
                    counter = (window.get("counters") or {}).get("model_cost_units") or {}
                    used, limit = int(counter.get("used") or 0), int(counter.get("limit") or 0)
                    day = {"known": True, "used": used, "limit": limit,
                           "left": (max(0, limit - used) if limit > 0 else None)}
        except Exception:  # noqa: BLE001 — an unreadable budget is unknown, not healthy
            day = {"known": False}
    return {"ts": moment.isoformat(), "providers": providers, "day_cost_units": day}


def model_roster_block(roster: dict[str, Any]) -> str:
    """The roster as prompt text. Facts only; the choice, if any, is the agent's."""
    intro = (
        "Your model providers, as the router and the ledgers see them right now "
        "(facts, not instructions; a key value is never shown):"
    )
    lines = ["<model_roster>", intro]
    for p in roster.get("providers", []):
        roles = ", ".join(p["roles"]) or "none"
        health = p["health"] + (f" ({p['unhealthy_reason']})" if p["unhealthy_reason"] else "")
        err = f", last error: {p['last_error_class']}" if p["last_error_class"] else ""
        tiers = "/".join(p["cost_tiers_seen"]) or "unmeasured"
        door = "" if p.get("door", True) else "; NO CLIENT IN THIS CODE — cannot be called"
        lines.append(
            f"- {p['provider']}: key {'present' if p['key_present'] else 'absent'}{door}; "
            f"roles: {roles}; health: {health}{err}; cost tier seen: {tiers}; "
            f"today: {p['calls_today']} calls, {p['tokens_today']} tokens, "
            f"{p['cost_units_today']} cost units"
        )
    day = roster.get("day_cost_units") or {}
    if day.get("known"):
        left = "unlimited" if day.get("left") is None else str(day["left"])
        lines.append(f"Day ceiling (model_cost_units): used {day['used']} of {day['limit']}, left {left}.")
    else:
        lines.append("Day ceiling: unknown (budget ledger not readable here).")
    lines.append("</model_roster>")
    return "\n".join(lines)
