"""Structured audit-logging for the mutating ``:refresh-models`` command.

``:refresh-models`` is the only model command that writes the on-disk catalog
(``config/model_catalog.json``). It previously left no structured trace in the
run-log while every read-only model command (registry-audit, discovery-audit,
provider-catalog-refresh) logged one. These tests pin the new symmetry:

  - a successful refresh logs ``refresh_models`` with safe payload;
  - a failed refresh logs ``refresh_models_failed`` and the handler still
    returns True (the REPL must not crash);
  - no secret (API key value) leaks into either payload;
  - no real provider/network call happens (fetchers are monkeypatched).
"""
from __future__ import annotations

import json
from pathlib import Path

import core.model_catalog as mc
from cli.commands_models import _handle_refresh_models


def _isolate(monkeypatch, tmp_path: Path) -> Path:
    cat = tmp_path / "model_catalog.json"
    monkeypatch.setenv("AGENT_MODEL_CATALOG_PATH", str(cat))
    for var in (
        "AGENT_MODEL_TIER_LIGHT",
        "AGENT_MODEL_TIER_STANDARD",
        "AGENT_MODEL_TIER_DEEP",
        "AGENT_MODEL_CATALOG_TTL_DAYS",
    ):
        monkeypatch.delenv(var, raising=False)
    return cat


def _fake_anthropic(api_key=None):
    return ["claude-opus-4-8", "claude-haiku-4-5", "claude-sonnet-4-6"]


class _FakeLog:
    def __init__(self):
        self.events = []

    def log(self, kind, payload):
        self.events.append((kind, payload))


class _FakeAgent:
    def __init__(self):
        self.log = _FakeLog()


def test_refresh_models_logs_success(monkeypatch, tmp_path, capsys):
    cat = _isolate(monkeypatch, tmp_path)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})
    agent = _FakeAgent()

    assert _handle_refresh_models("--anthropic", agent) is True
    # The write path still writes (behaviour unchanged).
    assert cat.exists()

    events = dict(agent.log.events)
    assert "refresh_models" in events
    payload = events["refresh_models"]
    assert payload["providers"] == ["anthropic"]
    assert payload["counts"]["anthropic"] == 3
    assert "tier_best" in payload and "anthropic" in payload["tier_best"]
    assert "updated_at" in payload


def test_refresh_models_logs_failure_and_returns_true(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)

    def _boom(*args, **kwargs):
        raise RuntimeError("provider unreachable")

    monkeypatch.setattr(mc, "refresh_catalog", _boom)
    agent = _FakeAgent()

    # Handler must swallow the error and keep the REPL alive.
    assert _handle_refresh_models("--anthropic", agent) is True

    events = dict(agent.log.events)
    assert "refresh_models" not in events
    assert "refresh_models_failed" in events
    payload = events["refresh_models_failed"]
    assert payload["providers"] == ["anthropic"]
    assert payload["error_type"] == "RuntimeError"
    assert "provider unreachable" in payload["error"]


def test_refresh_models_error_message_is_truncated(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)

    long_msg = "x" * 5000

    def _boom(*args, **kwargs):
        raise ValueError(long_msg)

    monkeypatch.setattr(mc, "refresh_catalog", _boom)
    agent = _FakeAgent()

    assert _handle_refresh_models("", agent) is True
    payload = dict(agent.log.events)["refresh_models_failed"]
    # Sanitized/truncated: never dump an unbounded error into the log.
    assert len(payload["error"]) <= 200


def test_refresh_models_success_payload_has_no_secret(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    secret = "sk-ant-TOP-SECRET-DO-NOT-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)
    monkeypatch.setattr(mc, "_FETCHERS", {"anthropic": _fake_anthropic})
    agent = _FakeAgent()

    assert _handle_refresh_models("--anthropic", agent) is True
    payload = dict(agent.log.events)["refresh_models"]
    assert secret not in json.dumps(payload, ensure_ascii=False)


def test_refresh_models_failure_payload_has_no_secret(monkeypatch, tmp_path, capsys):
    _isolate(monkeypatch, tmp_path)
    secret = "sk-ant-TOP-SECRET-DO-NOT-LEAK"
    monkeypatch.setenv("ANTHROPIC_API_KEY", secret)

    def _boom(*args, **kwargs):
        # An error that does NOT echo the secret; the payload must not add it.
        raise RuntimeError("auth failed")

    monkeypatch.setattr(mc, "refresh_catalog", _boom)
    agent = _FakeAgent()

    assert _handle_refresh_models("--anthropic", agent) is True
    payload = dict(agent.log.events)["refresh_models_failed"]
    assert secret not in json.dumps(payload, ensure_ascii=False)
