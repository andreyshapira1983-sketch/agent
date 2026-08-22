"""The router consults its own ledger before calling a provider that cannot pay.

WHY THIS EXISTS. Measured 2026-08-22 (MIR-132): Anthropic returned the literal
message "Your credit balance is too low" **391 consecutive times across four
days** — 226 of them on 2026-08-15 alone, against 239 successful OpenAI calls.
Per-call failover worked (214 of 215 affected runs were rescued), so no money
burned and little time was lost — but only because THIS provider fails fast.
One of those same calls took 152 seconds: a provider that hangs instead of
refusing would cost that on every attempt, because nothing remembered the last
391 outcomes. The root, as the registry words it: **presence of an API key is
treated as availability of the provider.**

THE SHAPE. Health is DERIVED from the usage ledger the router already writes —
no new state file, no new writer (the MIR-125 lesson: bounded tail read, not a
full-file scan). A provider is unhealthy when its most recent calls show
`_UNHEALTHY_AFTER` consecutive key/quota/billing-class failures with no
success since, the newest younger than a cooldown. Then `complete()` fails
over BEFORE the first doomed call, and the route_reason says so — the decision
is recorded beside its grounds (the provenance rule of 2026-08-22).

THE BOUNDARIES, each deliberate:
* only the key/quota/billing class demotes — a flaky network must not take a
  provider out (same conservatism as `_is_switch_key_error`);
* the demotion is a COOLDOWN, never a disable: past the window the provider is
  probed again — an autonomous actor may not permanently retire part of its
  own toolkit (the MIR-131/§9 authority line);
* one success heals everything;
* no ledger, or an unreadable one, reads as healthy — availability must not
  depend on a history file (fail open, unlike admission gates which fail
  closed: refusing to WORK is worse than one wasted probe call).
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.model_usage import ModelUsageLedger, ModelUsageLimits


def _iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat()


def _ledger(workspace: Path) -> ModelUsageLedger:
    return ModelUsageLedger(
        path=workspace / "data" / "model_usage.jsonl",
        limits=ModelUsageLimits(),
    )


def _record(ledger: ModelUsageLedger, *, provider: str, status: str,
            error: str | None = None, at: datetime) -> None:
    ledger.record(
        role="planner", provider=provider, model="m-1", route_reason="test",
        cost_tier="low", status=status, input_tokens=1, output_tokens=1,
        estimated=True, started_at=_iso(at), completed_at=_iso(at),
        duration_ms=10, error=error,
    )


_CREDIT = "BadRequestError: Your credit balance is too low to access the API."
_NOW = datetime(2026, 8, 22, 12, 0, tzinfo=timezone.utc)


def test_three_consecutive_credit_failures_mark_the_provider_unhealthy(
    workspace: Path,
) -> None:
    """The witness for the 391-call incident: after the third refusal the
    router must stop asking."""
    ledger = _ledger(workspace)
    for i in range(3):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(minutes=10 - i))

    reason = ledger.provider_unhealthy("anthropic", now=_NOW)
    assert reason, (
        "391 consecutive credit refusals and the router still treats the "
        "provider as available — a key is not a balance"
    )


def test_a_success_heals_the_provider(workspace: Path) -> None:
    ledger = _ledger(workspace)
    for i in range(3):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(minutes=30 - i))
    _record(ledger, provider="anthropic", status="success", at=_NOW - timedelta(minutes=5))

    assert ledger.provider_unhealthy("anthropic", now=_NOW) is None


def test_transient_network_errors_do_not_demote(workspace: Path) -> None:
    """The conservatism `_is_switch_key_error` already chose: a flaky network
    must not take a provider out of rotation."""
    ledger = _ledger(workspace)
    for i in range(5):
        _record(ledger, provider="openai", status="error",
                error="APIConnectionError: connection reset by peer",
                at=_NOW - timedelta(minutes=10 - i))

    assert ledger.provider_unhealthy("openai", now=_NOW) is None


def test_stale_failures_mean_a_probe_not_a_ban(workspace: Path) -> None:
    """The authority boundary: a cooldown, never a disable. Past the window
    the provider is tried again — the operator may have topped up the balance."""
    ledger = _ledger(workspace)
    for i in range(3):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(hours=3, minutes=10 - i))

    assert ledger.provider_unhealthy("anthropic", now=_NOW) is None


def test_two_failures_are_not_enough(workspace: Path) -> None:
    ledger = _ledger(workspace)
    for i in range(2):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(minutes=10 - i))

    assert ledger.provider_unhealthy("anthropic", now=_NOW) is None


def test_no_history_reads_as_healthy(workspace: Path) -> None:
    """Fail open: refusing to work is worse than one wasted probe call."""
    ledger = _ledger(workspace)
    assert ledger.provider_unhealthy("anthropic", now=_NOW) is None


def test_other_providers_history_is_not_charged_to_this_one(
    workspace: Path,
) -> None:
    ledger = _ledger(workspace)
    for i in range(4):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=_NOW - timedelta(minutes=10 - i))
    assert ledger.provider_unhealthy("openai", now=_NOW) is None


# ── the router consults it ───────────────────────────────────────────────────

class _DeadProviderLLM:
    """Raises like a provider with an empty balance; counts every call."""

    def __init__(self, provider: str, model: str) -> None:
        self.provider, self.model = provider, model
        self.calls = 0

    def complete(self, **_kw) -> str:
        self.calls += 1
        raise RuntimeError(_CREDIT)


class _AliveLLM:
    def __init__(self, provider: str, model: str) -> None:
        self.provider, self.model = provider, model
        self.calls = 0

    def complete(self, **_kw) -> str:
        self.calls += 1
        return "ok"


def test_the_router_skips_a_provider_its_ledger_knows_is_dead(
    workspace: Path, monkeypatch,
) -> None:
    """End to end: three recorded refusals, then a fresh router (a new process
    in real life) must go to the healthy provider WITHOUT spending a call on
    the dead one — that is precisely what 391 calls did not do."""
    from core.model_router import ModelRouter

    monkeypatch.setenv("AGENT_PROVIDER_FAILOVER", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ledger = _ledger(workspace)
    for i in range(3):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=datetime.now(timezone.utc) - timedelta(minutes=10 - i))

    dead = _DeadProviderLLM("anthropic", "claude-x")
    alive = _AliveLLM("openai", "gpt-x")

    def factory(provider, model):
        return dead if provider == "anthropic" else alive

    router = ModelRouter(
        default_provider="anthropic", default_model="claude-x",
        llm_factory=factory, usage_ledger=ledger,
    )
    out = router.for_role("planner").complete(system="s", user="u")

    assert out == "ok"
    assert dead.calls == 0, (
        f"the dead provider was still called {dead.calls} time(s) — the "
        "router did not consult its own ledger"
    )
    assert alive.calls == 1


def test_the_preemptive_switch_is_recorded_with_its_grounds(
    workspace: Path, monkeypatch,
) -> None:
    """Provenance: a silent re-route cannot be investigated later — the
    2026-08-16 rule. The ledger row for the substituted call must say WHY."""
    import json

    from core.model_router import ModelRouter

    monkeypatch.setenv("AGENT_PROVIDER_FAILOVER", "1")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")
    ledger = _ledger(workspace)
    for i in range(3):
        _record(ledger, provider="anthropic", status="error", error=_CREDIT,
                at=datetime.now(timezone.utc) - timedelta(minutes=10 - i))

    alive = _AliveLLM("openai", "gpt-x")
    router = ModelRouter(
        default_provider="anthropic", default_model="claude-x",
        llm_factory=lambda p, m: alive if p == "openai"
        else _DeadProviderLLM(p, m),
        usage_ledger=ledger,
    )
    router.for_role("planner").complete(system="s", user="u")

    rows = [json.loads(line) for line in
            (workspace / "data" / "model_usage.jsonl")
            .read_text(encoding="utf-8").splitlines() if line.strip()]
    payloads = [r.get("payload", r) for r in rows]
    last = [p for p in payloads if p.get("status") == "success"][-1]
    assert "provider_unhealthy" in str(last.get("route_reason", "")), (
        f"the pre-emptive switch left no grounds: {last.get('route_reason')!r}"
    )
