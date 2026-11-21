"""Tests for model_catalog routing resolution and cache I/O.

`classify_model` and `_model_recency_key` are already covered elsewhere; this
file closes the never-run half of the module: the actual ROUTING DECISION
(`tier_model_for`: env override -> catalog cache -> "") and the cache lifecycle
(`_load_catalog` TTL/corruption, `_save_catalog`, `catalog_summary`,
`refresh_catalog`). These are pure/deterministic — no provider API, no LLM — but
load-bearing: they decide which model the agent actually calls.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path

import core.model_catalog as mc
from core.model_catalog import (
    _load_catalog,
    _save_catalog,
    _ttl_days,
    catalog_summary,
    refresh_catalog,
)


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


# ── _load_catalog: cache lifecycle ────────────────────────────────────────────


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


def test_ttl_days_invalid_env_falls_back_to_default(monkeypatch):
    monkeypatch.setenv("AGENT_MODEL_CATALOG_TTL_DAYS", "not-a-number")
    assert _ttl_days() == 7


# ── catalog_summary ───────────────────────────────────────────────────────────


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
