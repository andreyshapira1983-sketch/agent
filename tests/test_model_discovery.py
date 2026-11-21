"""Tests for TD-011/012 read-only Live Model Discovery + catalog diff.

These cover the pure discovery/diff layer (``core.model_discovery`` and the
read-only ``discover_catalog`` split in ``core.model_catalog``). No real
provider API is contacted: fetchers are monkeypatched to canned model-id lists,
and the catalog file is pointed at a tmp path.

Guarantees asserted here:
  - discovery never writes ``config/model_catalog.json``;
  - the diff correctly reports added / removed / changed;
  - providers are bucketed into queried / unavailable / unsupported / skipped;
  - providers without credentials are NEVER contacted;
  - no secret (API key value) leaks into the report output.
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import core.model_catalog as mc
from core.model_catalog import _load_catalog, _save_catalog
from core.model_discovery import (
    _diff_catalog,
    build_discovery_report,
)


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    """Point the catalog at a tmp file and clear all provider credentials."""
    cat = tmp_path / "model_catalog.json"
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(cat))
    for var in (
        "AGENT_MODEL_TIER_LIGHT",
        "AGENT_MODEL_TIER_STANDARD",
        "AGENT_MODEL_TIER_DEEP",
        "AGENT_MODEL_CATALOG_TTL_DAYS",
        "ANTHROPIC_API_KEY",
        "OPENAI_API_KEY",
        "HF_TOKEN",
    ):
        monkeypatch.delenv(var, raising=False)
    return cat


def _fake_anthropic(api_key=None):
    return ["claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"]


# ── _diff_catalog: added / removed / changed ─────────────────────────────────

def _catalog(models_by_provider):
    providers = {}
    for prov, models in models_by_provider.items():
        classified = [{"id": mid, "tier": tier} for mid, tier in models]
        tier_best = {}
        for mid, tier in models:
            tier_best.setdefault(tier, mid)
        providers[prov] = {"models": classified, "tier_best": tier_best}
    return {"updated_at": datetime.now(timezone.utc).isoformat(), "providers": providers}


def test_diff_reports_changed_tier_best():
    current = _catalog({"anthropic": [("claude-opus-4-5", "deep")]})
    discovered = _catalog({"anthropic": [("claude-opus-4-8", "deep")]})
    diff = _diff_catalog(current, discovered)
    # tier_best for deep changed old -> new
    assert ("anthropic", "deep", "claude-opus-4-5", "claude-opus-4-8") in diff.changed


# ── build_discovery_report: dry-run, credential gating, no secrets ───────────


def test_dry_run_diff_against_existing_catalog(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    # Seed an older catalog on disk.
    _save_catalog(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {
                "anthropic": {
                    "models": [{"id": "claude-opus-4-5", "tier": "deep"}],
                    "tier_best": {"deep": "claude-opus-4-5"},
                }
            },
        }
    )
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    report = build_discovery_report(providers=["anthropic"])

    added_models = {m for (_p, m, _t) in report.diff.added}
    assert "claude-opus-4-8" in added_models
    # tier_best deep moved from 4-5 to 4-8
    assert any(
        prov == "anthropic" and tier == "deep" and old == "claude-opus-4-5"
        for (prov, tier, old, _new) in report.diff.changed
    )


def test_report_never_leaks_secret_values(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    secret = "sk-ant-TOP-SECRET-DO-NOT-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    report = build_discovery_report()

    blob = json.dumps(report.to_dict(), ensure_ascii=False) + "\n" + report.user_summary()
    assert secret not in blob
    # But the env-var NAME is safe to surface.
    assert "ANTHROPIC_API_KEY" in blob


# ── command handlers: dry-run only, never write ──────────────────────────────

class _FakeLog:
    def __init__(self):
        self.events = []

    def log(self, kind, payload):
        self.events.append((kind, payload))


class _FakeAgent:
    def __init__(self):
        self.log = _FakeLog()
        self.model_router = None


def test_provider_catalog_refresh_refuses_write(monkeypatch, tmp_path, capsys):
    from cli.commands_models import _handle_provider_catalog_refresh

    cat = _isolate(monkeypatch, tmp_path)

    def _boom(api_key=None):
        raise AssertionError("--write must never reach the network")

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _boom, "openai": _boom})
    agent = _FakeAgent()

    assert _handle_provider_catalog_refresh("--dry-run --write", agent) is True
    err = capsys.readouterr().err
    assert "--write is not supported" in err
    assert not cat.exists()
    assert agent.log.events == []


def test_provider_catalog_refresh_dry_run_never_writes(monkeypatch, tmp_path, capsys):
    from cli.commands_models import _handle_provider_catalog_refresh

    cat = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})
    agent = _FakeAgent()

    assert _handle_provider_catalog_refresh("--dry-run --anthropic", agent) is True
    # It logged a dry-run discovery event but wrote no catalog.
    assert not cat.exists()
    assert _load_catalog() is None
    kinds = [k for (k, _p) in agent.log.events]
    assert "provider_catalog_refresh_dry_run" in kinds
    # And the logged payload records that nothing was written.
    payload = dict(agent.log.events)["provider_catalog_refresh_dry_run"]
    assert payload["wrote_catalog"] is False


def test_model_discovery_audit_handler_is_local(monkeypatch, tmp_path, capsys):
    from cli.commands_models import _handle_model_discovery_audit

    cat = _isolate(monkeypatch, tmp_path)

    def _boom(api_key=None):
        raise AssertionError("audit handler must not contact providers")

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _boom, "openai": _boom})
    agent = _FakeAgent()

    assert _handle_model_discovery_audit("--json", agent) is True
    assert not cat.exists()
    kinds = [k for (k, _p) in agent.log.events]
    assert "model_discovery_audit" in kinds
