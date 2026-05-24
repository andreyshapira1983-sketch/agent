"""Tests for EmailTool's `default_dry_run` flag."""

from __future__ import annotations

from tools.builtins.email_tool import EmailTool


def _ok_params() -> dict:
    return {
        "to":      "alice@example.com",
        "subject": "test",
        "body":    "hi",
    }


def test_default_dry_run_true_simulates_send_when_unspecified():
    """Tool's default applies when caller omits the param."""
    tool = EmailTool(default_dry_run=True)
    result = tool.execute(**_ok_params())
    assert result.success
    assert result.output["mode"] == "dry_run"


def test_default_dry_run_false_would_attempt_real_send(monkeypatch):
    """Without credentials, the tool fails BEFORE touching SMTP."""
    tool = EmailTool(default_dry_run=False)
    # Clear out env so credentials look unset and the tool short-circuits
    monkeypatch.delenv("EMAIL_USERNAME", raising=False)
    monkeypatch.delenv("EMAIL_PASSWORD", raising=False)
    result = tool.execute(**_ok_params())
    assert not result.success
    assert "EMAIL_USERNAME" in result.error  # never reached SMTP layer


def test_explicit_dry_run_overrides_default():
    """Caller can still flip per-call regardless of runtime default."""
    tool = EmailTool(default_dry_run=False)
    result = tool.execute(**_ok_params(), dry_run=True)
    assert result.success
    assert result.output["mode"] == "dry_run"
