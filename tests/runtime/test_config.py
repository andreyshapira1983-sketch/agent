"""Tests for runtime.config — env loading, legacy aliasing, masking."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from brain.secrets import SecretsVault
from runtime.config import AgentConfig, _LEGACY_ALIASES, load_config, mask


@pytest.fixture
def fresh_env(monkeypatch, tmp_path):
    """Strip any inherited env vars that would interfere with these tests."""
    for name in (
        "OPENAI_API_KEY", "OPENAI_MODEL",
        "TELEGRAM_BOT_TOKEN", "TELEGRAM_ALERTS_CHAT_ID", "TELEGRAM",
        "EMAIL_USERNAME", "EMAIL_PASSWORD",
        "IMAP_USERNAME", "IMAP_PASSWORD", "IMAP_HOST", "IMAP_PORT",
        "AGENT_LOG_LEVEL", "AGENT_DRY_RUN",
    ):
        monkeypatch.delenv(name, raising=False)
    # Avoid CWD pollution
    monkeypatch.chdir(tmp_path)
    return tmp_path


def _write_env(path: Path, contents: str) -> None:
    path.write_text(contents, encoding="utf-8")


# ════════════════════════════════════════════════════════════════════
# mask
# ════════════════════════════════════════════════════════════════════

def test_mask_obscures_long_secrets():
    out = mask("sk-proj-abcdefghij12345")
    assert out.startswith("sk-p")
    assert "abcdef" not in out  # body never leaks
    assert out.endswith("...")


def test_mask_handles_empty():
    assert mask("") == "<unset>"
    assert mask(None) == "<unset>"


def test_mask_does_not_reveal_length():
    short = mask("sk-aaaa")
    long_ = mask("sk-aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa")
    assert short == long_, "mask must not differ based on secret length"


# ════════════════════════════════════════════════════════════════════
# load_config + alias logic
# ════════════════════════════════════════════════════════════════════

def test_load_config_reads_dotenv_and_marks_channels_ready(fresh_env, tmp_path):
    env = tmp_path / ".env"
    _write_env(env, "\n".join([
        "OPENAI_API_KEY=sk-test-1234567890",
        "TELEGRAM_BOT_TOKEN=8000000000:AAA",
        "EMAIL_USERNAME=me@example.com",
        "EMAIL_PASSWORD=pass-pass-pass",
    ]))
    cfg, vault = load_config(env_path=env)
    assert cfg.openai_ready is True
    assert cfg.telegram_ready is True
    assert cfg.email_ready is True
    # IMAP credentials are derived from EMAIL_*
    assert cfg.imap_ready is True
    assert vault.has("IMAP_USERNAME")
    assert vault.has("IMAP_PASSWORD")


def test_load_config_aliases_legacy_telegram(fresh_env, tmp_path):
    """The user's .env uses TELEGRAM=… — config must canonicalise it."""
    env = tmp_path / ".env"
    _write_env(env, "TELEGRAM=8000000000:AAA\n")
    cfg, vault = load_config(env_path=env)
    assert vault.has("TELEGRAM_BOT_TOKEN")
    assert cfg.telegram_ready is True


def test_load_config_does_not_overwrite_canonical(fresh_env, tmp_path):
    env = tmp_path / ".env"
    _write_env(env, "\n".join([
        "TELEGRAM=legacy-token",
        "TELEGRAM_BOT_TOKEN=canonical-token",
    ]))
    _cfg, vault = load_config(env_path=env)
    # canonical wins; legacy aliasing is no-op when canonical is set
    assert vault.reveal("TELEGRAM_BOT_TOKEN") == "canonical-token"


def test_load_config_handles_missing_env_file(fresh_env, tmp_path):
    cfg, vault = load_config(env_path=tmp_path / "no-such.env")
    assert not cfg.openai_ready
    assert not cfg.telegram_ready


def test_load_config_picks_up_shell_env(fresh_env, tmp_path, monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-shell")
    cfg, vault = load_config(env_path=tmp_path / ".env")
    assert cfg.openai_ready is True
    assert vault.has("OPENAI_API_KEY")


def test_load_config_does_not_log_warnings_for_already_loaded_keys(
    fresh_env, tmp_path, caplog,
):
    """The vault shouldn't complain about env-missing for keys it already has."""
    env = tmp_path / ".env"
    _write_env(env, "OPENAI_API_KEY=sk-test-1234567890\n")
    caplog.clear()
    load_config(env_path=env)
    warnings = [r.message for r in caplog.records if r.levelname == "WARNING"]
    assert not any("env missing 'OPENAI_API_KEY'" in w for w in warnings)


def test_config_summary_omits_raw_secrets(fresh_env, tmp_path):
    env = tmp_path / ".env"
    _write_env(env, "\n".join([
        "OPENAI_API_KEY=sk-proj-SUPER-SECRET-VALUE",
        "TELEGRAM=8000000000:AAA-LEAK-NOT",
    ]))
    cfg, _vault = load_config(env_path=env)
    blob = str(cfg.summary())
    assert "SUPER-SECRET-VALUE" not in blob
    assert "AAA-LEAK-NOT" not in blob


def test_aliases_dict_has_expected_keys():
    # Sanity: any rename of these names must be intentional.
    assert "TELEGRAM" in _LEGACY_ALIASES
    assert _LEGACY_ALIASES["TELEGRAM"] == "TELEGRAM_BOT_TOKEN"
