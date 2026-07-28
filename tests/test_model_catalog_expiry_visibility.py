"""An expired catalog must say so, not look like a missing model.

The catalog caches each provider's model list and maps a complexity tier to a
concrete model. It expires after `AGENT_MODEL_CATALOG_TTL_DAYS` days, which is
correct — provider line-ups change.

What was wrong is what expiry *looked like*. `_load_catalog` returned `None`,
`tier_model_for` returned `""`, and the escalation gate reported
`deep_downgraded:no_deep_model`. Read literally that says "the provider has no
model at this tier" — so the operator checks their provider, their credentials
and their registry, all of which are fine. The real cause is "the cached list is
12 days old, run :refresh-models", and nothing anywhere said it.

Measured on this repository: a catalog 12 days past a 7-day TTL silently
disabled complexity routing for **every** role that uses `for_task` — planner
and synthesizer included, not only repair. Every tier resolved to `""` and every
request fell back to the role default.

These tests pin the distinction, not the TTL: expiry stays, it just stops being
mute.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.task_complexity import ComplexityTier


def _catalog(path: Path, *, age_days: int) -> Path:
    stamp = (datetime.now(timezone.utc) - timedelta(days=age_days)).isoformat()
    path.write_text(json.dumps({
        "updated_at": stamp,
        "providers": {
            "anthropic": {
                "models": [{"id": "claude-opus-5", "tier": "deep"}],
                "tier_best": {"deep": "claude-opus-5", "standard": "claude-sonnet-5"},
            }
        },
    }), encoding="utf-8")
    return path


def _use(monkeypatch, path: Path) -> None:
    import core.model_catalog as catalog

    monkeypatch.setattr(catalog, "_catalog_path", lambda: path)


# ── the state is reportable at all ───────────────────────────────────────────

def test_a_fresh_catalog_reports_itself_usable(tmp_path, monkeypatch):
    from core.model_catalog import catalog_freshness

    _use(monkeypatch, _catalog(tmp_path / "c.json", age_days=0))
    state = catalog_freshness()

    assert state["status"] == "fresh"
    assert state["age_days"] == 0
    assert state["expired"] is False


def test_an_expired_catalog_reports_age_and_limit(tmp_path, monkeypatch):
    """Both numbers, so the message is actionable without opening the file."""
    from core.model_catalog import catalog_freshness

    _use(monkeypatch, _catalog(tmp_path / "c.json", age_days=12))
    state = catalog_freshness()

    assert state["status"] == "expired"
    assert state["expired"] is True
    assert state["age_days"] == 12
    assert state["ttl_days"] >= 1
    assert "refresh-models" in state["hint"], state["hint"]


def test_a_missing_catalog_is_its_own_state(tmp_path, monkeypatch):
    """"never built" and "went stale" need different answers from the operator."""
    from core.model_catalog import catalog_freshness

    _use(monkeypatch, tmp_path / "absent.json")
    state = catalog_freshness()

    assert state["status"] == "missing"
    assert state["expired"] is False, "absent is not expired — it was never written"
    assert "refresh-models" in state["hint"]


# ── and expiry still behaves exactly as before ───────────────────────────────

def test_an_expired_catalog_still_yields_no_tier_model(tmp_path, monkeypatch):
    """Visibility only. The TTL keeps its teeth."""
    from core.model_catalog import tier_model_for

    _use(monkeypatch, _catalog(tmp_path / "c.json", age_days=12))

    assert tier_model_for(ComplexityTier.DEEP, "anthropic") == ""


def test_a_fresh_catalog_still_resolves_the_tier(tmp_path, monkeypatch):
    from core.model_catalog import tier_model_for

    _use(monkeypatch, _catalog(tmp_path / "c.json", age_days=1))

    assert tier_model_for(ComplexityTier.DEEP, "anthropic") == "claude-opus-5"


# ── the gate distinguishes the two causes ────────────────────────────────────

def test_the_downgrade_reason_separates_stale_from_absent():
    """`no_deep_model` and `catalog_expired` send the operator to different places."""
    from core.deep_escalation import DeepEscalationRequest, evaluate_deep_escalation

    base = dict(
        role="repair_proposal",
        reason="high_value_repair",
        expected_output="minimal_patch_plan",
        budget_ok=True,
        operator_approved=False,
    )

    absent = evaluate_deep_escalation(
        DeepEscalationRequest(deep_model_available=False, **base)
    )
    stale = evaluate_deep_escalation(
        DeepEscalationRequest(deep_model_available=False,
                              catalog_expired=True, **base)
    )

    assert absent.downgraded and stale.downgraded
    assert absent.route_reason == "deep_downgraded:no_deep_model"
    assert stale.route_reason == "deep_downgraded:catalog_expired", (
        "a stale cache is reported as a missing model, which sends the operator "
        "to check their provider instead of running :refresh-models"
    )
