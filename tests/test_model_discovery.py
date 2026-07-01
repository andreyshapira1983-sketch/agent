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
from core.model_catalog import discover_catalog, _load_catalog, _save_catalog
from core.model_discovery import (
    build_discovery_audit,
    build_discovery_report,
    _diff_catalog,
    STATUS_UNSUPPORTED_NO_FETCHER,
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


def _fake_openai(api_key=None):
    return ["gpt-5-mini", "gpt-5", "o5"]


# ── discover_catalog: read-only, no write ─────────────────────────────────────

def test_discover_catalog_does_not_write_file(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    result = discover_catalog(["anthropic"])

    # Classified data is returned...
    assert result["providers"]["anthropic"]["tier_best"]["deep"] == "claude-opus-4-8"
    # ...but nothing was written to disk.
    assert not cat.exists()
    assert _load_catalog() is None


def test_refresh_catalog_still_writes(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    mc.refresh_catalog(["anthropic"])

    # The write path (unchanged) still persists the catalog.
    assert cat.exists()
    assert _load_catalog() is not None


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


def test_diff_reports_added():
    current = _catalog({"openai": [("gpt-5", "standard")]})
    discovered = _catalog({"openai": [("gpt-5", "standard"), ("gpt-6", "standard")]})
    diff = _diff_catalog(current, discovered)
    assert ("openai", "gpt-6", "standard") in diff.added
    assert diff.removed == ()


def test_diff_reports_removed():
    current = _catalog({"openai": [("gpt-5", "standard"), ("gpt-4", "standard")]})
    discovered = _catalog({"openai": [("gpt-5", "standard")]})
    diff = _diff_catalog(current, discovered)
    assert ("openai", "gpt-4", "standard") in diff.removed
    assert diff.added == ()


def test_diff_reports_changed_tier_best():
    current = _catalog({"anthropic": [("claude-opus-4-5", "deep")]})
    discovered = _catalog({"anthropic": [("claude-opus-4-8", "deep")]})
    diff = _diff_catalog(current, discovered)
    # tier_best for deep changed old -> new
    assert ("anthropic", "deep", "claude-opus-4-5", "claude-opus-4-8") in diff.changed


def test_diff_against_no_current_is_all_added():
    discovered = _catalog({"openai": [("gpt-5", "standard")]})
    diff = _diff_catalog(None, discovered)
    assert ("openai", "gpt-5", "standard") in diff.added
    assert diff.removed == ()
    assert diff.changed == ()


# ── build_discovery_audit: local only, no network ────────────────────────────

def test_audit_is_local_and_writes_nothing(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)

    # Any fetcher call would raise; the audit must never call one.
    def _boom(api_key=None):
        raise AssertionError("audit must not contact providers")

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _boom, "openai": _boom})

    report = build_discovery_audit()

    assert report.live is False
    assert report.to_dict()["wrote_catalog"] is False
    assert not cat.exists()
    # mock is supported but has no fetcher -> unsupported bucket.
    assert "mock" in report.unsupported


# ── build_discovery_report: dry-run, credential gating, no secrets ───────────

def test_dry_run_queries_only_providers_with_credentials(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    # No OPENAI_API_KEY on purpose.

    called = {"openai": False}

    def _openai_should_not_run(api_key=None):
        called["openai"] = True
        raise AssertionError("openai has no creds and must not be queried")

    monkeypatch.setattr(
        mc, "_FETCHERS", {"anthropic": _fake_anthropic, "openai": _openai_should_not_run}
    )

    report = build_discovery_report()

    assert called["openai"] is False
    assert "anthropic" in report.queried
    assert "openai" in report.unavailable  # supported + fetcher, but no creds


def test_dry_run_does_not_write_catalog(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-secret-value")
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    report = build_discovery_report()

    assert report.live is True
    assert not cat.exists()
    assert _load_catalog() is None


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


def test_unsupported_provider_reported_when_no_fetcher(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic, "openai": _fake_openai})

    # No provider filter: huggingface is supported by the adapter but has no
    # live fetcher, so it is classified unsupported-for-discovery (not skipped).
    report = build_discovery_report()
    hf = next(p for p in report.providers if p.name == "huggingface")
    assert hf.status == STATUS_UNSUPPORTED_NO_FETCHER
    assert "huggingface" in report.unsupported


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


def test_provider_catalog_refresh_requires_dry_run(monkeypatch, tmp_path, capsys):
    from cli.commands_models import _handle_provider_catalog_refresh

    cat = _isolate(monkeypatch, tmp_path)

    def _boom(api_key=None):
        raise AssertionError("must not contact providers without --dry-run")

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _boom, "openai": _boom})
    agent = _FakeAgent()

    assert _handle_provider_catalog_refresh("", agent) is True
    # No network, no write, no logged discovery event.
    assert not cat.exists()
    assert agent.log.events == []


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
