"""Model routing / catalog / usage commands — what may touch the network, and what may not.

`cli/commands_models.py` holds six commands, and two of them can leave the
machine: `:refresh-models` queries providers and writes the catalog,
`:provider-catalog-refresh` queries providers and writes nothing. The rest are
local reports.

What these tests hold onto:

* `:provider-catalog-refresh` contacts **no provider** unless `--dry-run` is
  given, and refuses `--write` outright — the discovery fake fails the test if
  it is ever reached without the flag;
* option flags (`--json`, `--dry-run`, `--write`) are never mistaken for
  provider selectors, so `--json` alone cannot narrow a refresh to a provider
  named "json";
* a failed refresh logs a structured record with a truncated message and the
  exception type — never a key, never an env value;
* `:model-usage` copes with a ledger that is switched off.
"""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

import cli.commands_models as mod
from cli.commands_models import (
    _handle_model_discovery_audit,
    _handle_model_registry_audit,
    _handle_model_usage,
    _handle_models,
    _handle_provider_catalog_refresh,
    _handle_refresh_models,
)


class FakeLog:
    def __init__(self) -> None:
        self.events: list[tuple[str, dict]] = []

    def log(self, event, payload=None):
        self.events.append((event, payload))

    def kinds(self):
        return [e for e, _ in self.events]

    def payload(self, event):
        for name, data in self.events:
            if name == event:
                return data or {}
        raise AssertionError(f"{event!r} not logged; got {self.kinds()}")


ROUTES = {"planner": {"provider": "anthropic", "model": "sonnet", "reason": "policy"}}
REGISTRY = {
    "selection_policy": {
        "name": "balanced",
        "max_cost_tier": "standard",
        "allow_mock": False,
        "require_available": True,
    },
    "models": [
        {
            "id": "anthropic-default",
            "provider": "anthropic",
            "model": "sonnet",
            "roles": ["planner", "synthesizer"],
            "cost_tier": "standard",
            "quality_tier": "high",
            "provider_supported": True,
            "available": True,
            "source": "builtin",
        }
    ],
}


@pytest.fixture
def agent():
    return SimpleNamespace(
        log=FakeLog(),
        model_router=SimpleNamespace(
            routing_summary=lambda: ROUTES,
            registry_summary=lambda: REGISTRY,
            usage_snapshot=lambda: None,
        ),
    )


def never(*a, **k):
    raise AssertionError("this collaborator must not be reached")


# ── :models ──────────────────────────────────────────────────────────────────

def test_models_rejects_unknown_flags(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "catalog_summary", never)
    assert _handle_models("--verbose", agent) is True
    assert "Usage: :models [--json]" in capsys.readouterr().err


def test_models_prints_routes_policy_registry_and_catalog(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    monkeypatch.setattr(
        catalog_mod,
        "catalog_summary",
        lambda: {
            "status": "ok",
            "updated_at": "2026-07-01",
            "providers": {"anthropic": {"model_count": 3, "tier_best": {"deep": "opus"}}},
        },
    )

    assert _handle_models("", agent) is True

    err = capsys.readouterr().err
    assert "=== model routes ===" in err
    assert "planner: provider=anthropic model=sonnet reason=policy" in err
    assert "name=balanced max_cost=standard" in err
    assert "anthropic-default provider=anthropic" in err
    assert "roles=planner,synthesizer" in err
    assert "=== model catalog (updated 2026-07-01) ===" in err
    assert "anthropic (3 models): deep=opus" in err


def test_models_says_when_the_catalog_is_empty(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "catalog_summary", lambda: {"status": "no_catalog"})

    assert _handle_models("", agent) is True
    assert "(not populated — run :refresh-models)" in capsys.readouterr().err


def test_models_json_carries_all_three_sections(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    monkeypatch.setattr(catalog_mod, "catalog_summary", lambda: {"status": "no_catalog"})

    assert _handle_models("--json", agent) is True
    payload = json.loads(capsys.readouterr().err)
    assert set(payload) == {"routes", "registry", "catalog"}
    assert payload["routes"] == ROUTES


# ── :model-registry-audit ────────────────────────────────────────────────────

def test_registry_audit_rejects_unknown_flags(agent, capsys, monkeypatch):
    monkeypatch.setattr(mod, "audit_model_registry", never)
    assert _handle_model_registry_audit("--all", agent) is True
    assert "Usage: :model-registry-audit [--json]" in capsys.readouterr().err
    assert agent.log.kinds() == []


def test_registry_audit_logs_and_prints_both_modes(agent, capsys, monkeypatch):
    monkeypatch.setattr(
        mod,
        "audit_model_registry",
        lambda router: SimpleNamespace(
            to_dict=lambda: {"issues": []}, user_summary=lambda: "AUDIT-TEXT"
        ),
    )

    assert _handle_model_registry_audit("", agent) is True
    assert "AUDIT-TEXT" in capsys.readouterr().err

    assert _handle_model_registry_audit("--json", agent) is True
    assert json.loads(capsys.readouterr().err) == {"issues": []}
    assert agent.log.kinds() == ["model_registry_audit", "model_registry_audit"]


# ── :refresh-models ──────────────────────────────────────────────────────────

def _catalog(**providers):
    return {"updated_at": "2026-07-26", "providers": providers}


def test_refresh_models_forwards_only_provider_flags(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    seen: dict = {}

    def _refresh(*, providers):
        seen["providers"] = providers
        return _catalog()

    monkeypatch.setattr(catalog_mod, "refresh_catalog", _refresh)

    assert _handle_refresh_models("--anthropic --openai", agent) is True
    assert seen["providers"] == ["anthropic", "openai"]


def test_refresh_models_without_flags_asks_for_every_provider(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    seen: dict = {}
    monkeypatch.setattr(
        catalog_mod, "refresh_catalog", lambda *, providers: seen.setdefault("p", providers) or _catalog()
    )

    assert _handle_refresh_models("", agent) is True
    assert seen["p"] is None, "no flags means every available provider"


def test_refresh_models_reports_and_logs_a_failure_without_leaking(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    secret = "sk-ant-SUPER-SECRET-" + "x" * 300

    def _boom(*, providers):
        raise RuntimeError(f"401 unauthorized for key {secret}")

    monkeypatch.setattr(catalog_mod, "refresh_catalog", _boom)

    assert _handle_refresh_models("--anthropic", agent) is True

    err = capsys.readouterr().err
    assert "refresh-models" in err and "401 unauthorized" in err

    logged = agent.log.payload("refresh_models_failed")
    assert logged["error_type"] == "RuntimeError"
    assert logged["providers"] == ["anthropic"]
    assert len(logged["error"]) <= 200, "the logged message is truncated"


def test_refresh_models_logs_counts_and_tier_bests(agent, capsys, monkeypatch):
    import core.model_catalog as catalog_mod

    catalog = _catalog(
        anthropic={
            "models": [
                {"id": "opus-4", "tier": "deep"},
                {"id": "opus-3", "tier": "deep"},
                {"id": "haiku-4", "tier": "light"},
            ],
            "tier_best": {"deep": "opus-4", "light": "haiku-4"},
        }
    )
    monkeypatch.setattr(catalog_mod, "refresh_catalog", lambda *, providers: catalog)

    assert _handle_refresh_models("", agent) is True

    logged = agent.log.payload("refresh_models")
    assert logged["counts"] == {"anthropic": 3}
    assert logged["tier_best"] == {"anthropic": {"deep": "opus-4", "light": "haiku-4"}}
    assert logged["updated_at"] == "2026-07-26"

    err = capsys.readouterr().err
    assert "ANTHROPIC" in err
    assert "opus-4" in err
    assert "opus-3" in err, "a runner-up in the same tier is listed as an alternative"
    assert "config/model_catalog.json" in err


# ── :model-usage ─────────────────────────────────────────────────────────────

def test_model_usage_rejects_unknown_flags(agent, capsys):
    assert _handle_model_usage("--today", agent) is True
    assert "Usage: :model-usage [--json]" in capsys.readouterr().err


def test_model_usage_without_a_ledger(agent, capsys):
    assert _handle_model_usage("", agent) is True
    assert "(model usage ledger is not enabled)" in capsys.readouterr().err


def _snapshot():
    return {
        "totals": {"calls": 10, "total_tokens": 1000, "cost_units": 5},
        "session_totals": {"calls": 2, "total_tokens": 200, "cost_units": 1},
        "by_role": {"planner": {"calls": 6, "total_tokens": 600, "cost_units": 3}},
        "by_model": {"sonnet": {"calls": 10, "total_tokens": 1000, "cost_units": 5}},
    }


def test_model_usage_separates_history_from_session(agent, capsys):
    agent.model_router.usage_snapshot = _snapshot

    assert _handle_model_usage("", agent) is True

    err = capsys.readouterr().err
    assert "history_calls=10 history_tokens=1000 history_cost_units=5" in err
    assert "session_calls=2 session_tokens=200 session_cost_units=1" in err
    assert "planner: calls=6" in err
    assert "sonnet: calls=10" in err


def test_model_usage_json(agent, capsys):
    agent.model_router.usage_snapshot = _snapshot
    assert _handle_model_usage("--json", agent) is True
    assert json.loads(capsys.readouterr().err) == _snapshot()


# ── :model-discovery-audit (local, no network) ───────────────────────────────

def test_discovery_audit_rejects_unknown_flags(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    monkeypatch.setattr(discovery_mod, "build_discovery_audit", never)
    assert _handle_model_discovery_audit("--live", agent) is True
    assert "Usage: :model-discovery-audit [--json]" in capsys.readouterr().err


def test_discovery_audit_is_local_and_logs(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    seen: list = []
    monkeypatch.setattr(
        discovery_mod,
        "build_discovery_audit",
        lambda router: seen.append(router)
        or SimpleNamespace(to_dict=lambda: {"providers": []}, user_summary=lambda: "DISCOVERY"),
    )

    assert _handle_model_discovery_audit("", agent) is True
    assert seen == [agent.model_router], "the audit inspects the live router"
    assert "DISCOVERY" in capsys.readouterr().err

    assert _handle_model_discovery_audit("--json", agent) is True
    assert json.loads(capsys.readouterr().err) == {"providers": []}
    assert agent.log.kinds() == ["model_discovery_audit", "model_discovery_audit"]


# ── :provider-catalog-refresh — the network gate ─────────────────────────────

@pytest.mark.parametrize("rest", ["", "--anthropic", "--json", "--openai --json"])
def test_no_provider_is_contacted_without_dry_run(rest, agent, capsys, monkeypatch):
    """The flag is the gate: without it, nothing may leave the machine."""
    import core.model_discovery as discovery_mod

    monkeypatch.setattr(discovery_mod, "build_discovery_report", never)

    assert _handle_provider_catalog_refresh(rest, agent) is True

    err = capsys.readouterr().err
    assert "Usage: :provider-catalog-refresh --dry-run" in err
    assert "--dry-run is required" in err
    assert agent.log.kinds() == []


def test_write_flag_is_refused_before_anything_else(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    monkeypatch.setattr(discovery_mod, "build_discovery_report", never)

    assert _handle_provider_catalog_refresh("--dry-run --write --anthropic", agent) is True

    err = capsys.readouterr().err
    assert "--write is not supported here" in err
    assert "Use :refresh-models" in err
    assert agent.log.kinds() == []


def test_dry_run_queries_only_the_named_providers(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    seen: dict = {}

    def _report(*, providers):
        seen["providers"] = providers
        return SimpleNamespace(to_dict=lambda: {"diff": []}, user_summary=lambda: "DIFF-TEXT")

    monkeypatch.setattr(discovery_mod, "build_discovery_report", _report)

    assert _handle_provider_catalog_refresh("--dry-run --anthropic --json", agent) is True

    assert seen["providers"] == ["anthropic"], "--json and --dry-run are options, not providers"
    assert json.loads(capsys.readouterr().err) == {"diff": []}
    assert agent.log.kinds() == ["provider_catalog_refresh_dry_run"]


def test_dry_run_without_a_provider_flag_asks_for_all(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    seen: dict = {}

    def _report(*, providers):
        seen["providers"] = providers
        return SimpleNamespace(to_dict=lambda: {}, user_summary=lambda: "DIFF-TEXT")

    monkeypatch.setattr(discovery_mod, "build_discovery_report", _report)

    assert _handle_provider_catalog_refresh("--dry-run", agent) is True

    assert seen["providers"] is None
    assert "DIFF-TEXT" in capsys.readouterr().err


def test_dry_run_reports_a_provider_failure_without_raising(agent, capsys, monkeypatch):
    import core.model_discovery as discovery_mod

    def _boom(*, providers):
        raise TimeoutError("provider did not answer")

    monkeypatch.setattr(discovery_mod, "build_discovery_report", _boom)

    assert _handle_provider_catalog_refresh("--dry-run", agent) is True

    err = capsys.readouterr().err
    assert "provider-catalog-refresh error: provider did not answer" in err
    assert "ANTHROPIC_API_KEY" in err
    assert agent.log.kinds() == [], "a failed dry run records no catalog event"
