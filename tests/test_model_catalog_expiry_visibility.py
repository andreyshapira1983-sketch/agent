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

# ── the gate distinguishes the two causes ────────────────────────────────────

def _gate(**overrides):
    from core.deep_escalation import DeepEscalationRequest, evaluate_deep_escalation

    base = {
        "role": "repair_proposal",
        "reason": "high_value_repair",
        "expected_output": "minimal_patch_plan",
        "budget_ok": True,
        "operator_approved": False,
        "deep_model_available": False,
    }
    base.update(overrides)
    return evaluate_deep_escalation(DeepEscalationRequest(**base))


def test_the_downgrade_reason_separates_cache_state_from_provider_fact():
    """`no_deep_model` and a cache state send the operator to different places."""
    provider_really_has_none = _gate(catalog_status=None)
    stale = _gate(catalog_status="expired")

    assert provider_really_has_none.downgraded and stale.downgraded
    assert provider_really_has_none.route_reason == "deep_downgraded:no_deep_model"
    assert stale.route_reason == "deep_downgraded:catalog_expired", (
        "a stale cache is reported as a missing model, which sends the operator "
        "to check their provider instead of running :refresh-models"
    )


def test_a_fresh_cache_does_not_excuse_a_missing_model():
    """With the cache healthy, an empty tier really is a provider fact."""
    assert _gate(catalog_status="fresh").route_reason == "deep_downgraded:no_deep_model"
