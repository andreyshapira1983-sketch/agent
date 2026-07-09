"""Tests for model_catalog routing resolution and cache I/O.

`classify_model` and `_model_recency_key` are already covered elsewhere; this
file closes the never-run half of the module: the actual ROUTING DECISION
(`tier_model_for`: env override -> catalog cache -> "") and the cache lifecycle
(`_load_catalog` TTL/corruption, `_save_catalog`, `catalog_summary`,
`refresh_catalog`). These are pure/deterministic — no provider API, no LLM — but
load-bearing: they decide which model the agent actually calls.
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

import core.model_catalog as mc
from core.model_catalog import (
    catalog_summary,
    refresh_catalog,
    tier_model_for,
    _load_catalog,
    _save_catalog,
    _ttl_days,
    _catalog_path,
)
from core.task_complexity import ComplexityTier


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    """Point the catalog at a tmp file and clear operator tier overrides."""
    cat = tmp_path / "model_catalog.json"
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(cat))
    for var in (
        "AGENT_MODEL_TIER_LIGHT",
        "AGENT_MODEL_TIER_STANDARD",
        "AGENT_MODEL_TIER_DEEP",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.delenv("AGENT_MODEL_CATALOG_TTL_DAYS", raising=False)
    return cat


# ── tier_model_for: the routing decision ──────────────────────────────────────

def test_env_override_wins_over_catalog(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)
    # A catalog exists and would resolve to a different model...
    _save_catalog(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {
                "anthropic": {"models": [], "tier_best": {"deep": "from-catalog"}}
            },
        }
    )
    # ...but the operator override takes precedence.
    monkeypatch.setenv("AGENT_MODEL_TIER_DEEP", "operator-pinned-model")
    assert tier_model_for(ComplexityTier.DEEP, "anthropic") == "operator-pinned-model"
    assert cat.exists()


def test_catalog_used_when_no_env_override(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _save_catalog(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {
                "anthropic": {
                    "models": [],
                    "tier_best": {"deep": "claude-opus-4-8", "light": "claude-haiku-4-5"},
                }
            },
        }
    )
    assert tier_model_for(ComplexityTier.DEEP, "anthropic") == "claude-opus-4-8"
    assert tier_model_for(ComplexityTier.LIGHT, "anthropic") == "claude-haiku-4-5"


def test_returns_empty_when_nothing_configured(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    # No env override, no catalog file at all -> "" (caller uses its default).
    assert tier_model_for(ComplexityTier.STANDARD, "anthropic") == ""


def test_returns_empty_when_provider_absent_from_catalog(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _save_catalog(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {"anthropic": {"models": [], "tier_best": {"deep": "x"}}},
        }
    )
    # openai is not in the catalog -> "" rather than a crash.
    assert tier_model_for(ComplexityTier.DEEP, "openai") == ""


# ── _load_catalog: cache lifecycle ────────────────────────────────────────────

def test_load_catalog_missing_returns_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert _load_catalog() is None


def test_load_catalog_expired_returns_none(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    stale = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()
    _save_catalog({"updated_at": stale, "providers": {}})
    # Default TTL is 7 days -> a 30-day-old catalog is expired.
    assert _load_catalog() is None


def test_load_catalog_fresh_is_returned(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    fresh = datetime.now(timezone.utc).isoformat()
    _save_catalog({"updated_at": fresh, "providers": {"anthropic": {}}})
    loaded = _load_catalog()
    assert loaded is not None
    assert "anthropic" in loaded["providers"]


def test_load_catalog_corrupt_returns_none(monkeypatch, tmp_path):
    cat = _isolate(monkeypatch, tmp_path)
    cat.parent.mkdir(parents=True, exist_ok=True)
    cat.write_text("{not valid json", encoding="utf-8")
    assert _load_catalog() is None


def test_load_catalog_without_updated_at_is_returned(monkeypatch, tmp_path):
    # No updated_at means we cannot prove staleness; the cache is still usable.
    _isolate(monkeypatch, tmp_path)
    _save_catalog({"providers": {"openai": {}}})
    loaded = _load_catalog()
    assert loaded is not None
    assert "openai" in loaded["providers"]


# ── _ttl_days / _catalog_path ─────────────────────────────────────────────────

def test_ttl_days_default_when_unset(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    assert _ttl_days() == 7


def test_ttl_days_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_CATALOG_TTL_DAYS", "not-a-number")
    assert _ttl_days() == 7


def test_catalog_path_honours_env_override(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", "/tmp/custom_catalog.json")
    assert _catalog_path() == Path("/tmp/custom_catalog.json")


# ── catalog_summary ───────────────────────────────────────────────────────────

def test_catalog_summary_no_catalog(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    summary = catalog_summary()
    assert summary["status"] == "no_catalog"
    assert "refresh-models" in summary["hint"]


def test_catalog_summary_reports_counts_and_tier_best(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    _save_catalog(
        {
            "updated_at": datetime.now(timezone.utc).isoformat(),
            "providers": {
                "anthropic": {
                    "models": [
                        {"id": "claude-opus-4-8", "tier": "deep"},
                        {"id": "claude-haiku-4-5", "tier": "light"},
                    ],
                    "tier_best": {"deep": "claude-opus-4-8", "light": "claude-haiku-4-5"},
                }
            },
        }
    )
    summary = catalog_summary()
    assert summary["providers"]["anthropic"]["model_count"] == 2
    assert summary["providers"]["anthropic"]["tier_best"]["deep"] == "claude-opus-4-8"


# ── refresh_catalog ───────────────────────────────────────────────────────────

def test_refresh_catalog_classifies_and_picks_best_per_tier(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    def _fake_anthropic(api_key=None):
        return ["claude-opus-4-1", "claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"]

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})

    catalog = refresh_catalog(["anthropic"])

    prov = catalog["providers"]["anthropic"]
    # Newest opus wins for DEEP; recency by version (4-8 > 4-1).
    assert prov["tier_best"]["deep"] == "claude-opus-4-8"
    assert prov["tier_best"]["light"] == "claude-haiku-4-5"
    assert prov["tier_best"]["standard"] == "claude-sonnet-4-6"
    # Saved to disk and re-loadable.
    assert _load_catalog() is not None


def test_refresh_catalog_skips_unknown_provider(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mc, "_FETCHERS", {})
    catalog = refresh_catalog(["does-not-exist"])
    assert catalog["providers"] == {}


def test_refresh_catalog_tolerates_fetcher_failure(monkeypatch, tmp_path):
    _isolate(monkeypatch, tmp_path)

    def _boom(api_key=None):
        raise RuntimeError("provider down")

    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _boom})
    catalog = refresh_catalog(["anthropic"])
    # A failed fetch is skipped, not fatal; provider simply absent.
    assert "anthropic" not in catalog["providers"]
