"""Tests for brain.secrets — Secret wrapper and SecretsVault."""

from __future__ import annotations

import logging
import os
from pathlib import Path

import pytest

from brain.secrets import (
    Secret,
    SecretNotFoundError,
    SecretsVault,
)


# ────────────────────────────────────────────────────────────────────
# Secret wrapper
# ────────────────────────────────────────────────────────────────────

class TestSecretSafety:
    """The Secret class must never expose its value through str/repr/format."""

    def test_str_returns_mask(self):
        s = Secret("super-secret-key", name="API_KEY")
        assert str(s) == "***"

    def test_repr_returns_mask_with_name(self):
        s = Secret("super-secret-key", name="API_KEY")
        assert "***" in repr(s)
        assert "API_KEY" in repr(s)
        assert "super-secret-key" not in repr(s)

    def test_f_string_does_not_leak(self):
        s = Secret("super-secret-key", name="API_KEY")
        formatted = f"key={s}"
        assert "super-secret-key" not in formatted
        assert "***" in formatted

    def test_format_with_spec_does_not_leak(self):
        s = Secret("super-secret-key", name="API_KEY")
        assert "super-secret-key" not in f"{s:>20}"
        assert "super-secret-key" not in f"{s!s}"

    def test_reveal_returns_real_value(self):
        s = Secret("super-secret-key", name="API_KEY")
        assert s.reveal() == "super-secret-key"

    def test_logging_does_not_leak_secret(self, caplog):
        s = Secret("super-secret-key", name="API_KEY")
        log = logging.getLogger("test_leak")
        with caplog.at_level(logging.INFO, logger="test_leak"):
            log.info("loaded key %s", s)
            log.info(f"loaded key {s}")
        for record in caplog.records:
            assert "super-secret-key" not in record.getMessage()

    def test_exception_message_does_not_leak(self):
        s = Secret("super-secret-key", name="API_KEY")
        try:
            raise ValueError(f"bad secret: {s}")
        except ValueError as exc:
            assert "super-secret-key" not in str(exc)
            assert "***" in str(exc)


class TestSecretBehaviour:

    def test_equality_by_value(self):
        a = Secret("x", "A")
        b = Secret("x", "B")
        c = Secret("y", "A")
        assert a == b
        assert a != c

    def test_equality_against_str_is_not_implemented(self):
        s = Secret("x", "A")
        assert s != "x"          # never equate Secret with raw str
        assert not (s == "x")

    def test_bool(self):
        assert bool(Secret("x"))
        assert not bool(Secret(""))

    def test_len(self):
        assert len(Secret("abc")) == 3

    def test_type_check_rejects_non_string(self):
        with pytest.raises(TypeError):
            Secret(12345, "API_KEY")  # type: ignore[arg-type]


# ────────────────────────────────────────────────────────────────────
# Vault
# ────────────────────────────────────────────────────────────────────

class TestSecretsVaultBasics:

    def test_set_and_get(self):
        v = SecretsVault()
        v.set("FOO", "bar")
        assert v.has("FOO")
        assert "FOO" in v
        assert v.get("FOO").reveal() == "bar"

    def test_get_missing_raises(self):
        v = SecretsVault()
        with pytest.raises(SecretNotFoundError):
            v.get("MISSING")

    def test_get_optional_returns_none(self):
        v = SecretsVault()
        assert v.get_optional("MISSING") is None

    def test_reveal_shortcut(self):
        v = SecretsVault()
        v.set("X", "y")
        assert v.reveal("X") == "y"

    def test_names_sorted(self):
        v = SecretsVault()
        v.set("B", "1")
        v.set("A", "2")
        v.set("C", "3")
        assert v.names() == ["A", "B", "C"]

    def test_repr_does_not_leak_values(self):
        v = SecretsVault()
        v.set("API_KEY", "super-secret")
        rep = repr(v)
        assert "super-secret" not in rep
        assert "API_KEY" in rep

    def test_len(self):
        v = SecretsVault()
        assert len(v) == 0
        v.set("A", "1")
        v.set("B", "2")
        assert len(v) == 2


class TestSecretsVaultFromEnv:

    def test_load_from_env(self, monkeypatch):
        monkeypatch.setenv("TEST_KEY_1", "value-1")
        monkeypatch.setenv("TEST_KEY_2", "value-2")

        v = SecretsVault()
        v.load_from_env("TEST_KEY_1", "TEST_KEY_2")

        assert v.reveal("TEST_KEY_1") == "value-1"
        assert v.reveal("TEST_KEY_2") == "value-2"

    def test_load_from_env_skips_missing_when_optional(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        v = SecretsVault()
        v.load_from_env("MISSING_KEY", required=False)
        assert not v.has("MISSING_KEY")

    def test_load_from_env_raises_when_required(self, monkeypatch):
        monkeypatch.delenv("MISSING_KEY", raising=False)
        v = SecretsVault()
        with pytest.raises(SecretNotFoundError):
            v.load_from_env("MISSING_KEY", required=True)

    def test_load_from_env_strips_whitespace(self, monkeypatch):
        monkeypatch.setenv("PADDED", "   value-with-padding   ")
        v = SecretsVault()
        v.load_from_env("PADDED")
        assert v.reveal("PADDED") == "value-with-padding"


class TestSecretsVaultFromDotenv:

    def test_load_dotenv(self, tmp_path: Path):
        env_file = tmp_path / ".env"
        env_file.write_text(
            "# comment line\n"
            "FOO=bar\n"
            'QUOTED="quoted-value"\n'
            "SINGLE='single-value'\n"
            "EMPTY=\n"
            "\n"
            "WHITESPACE=  padded  \n",
            encoding="utf-8",
        )
        v = SecretsVault()
        loaded = v.load_dotenv(env_file)

        # dotenv keeps EMPTY out (we skip empty values)
        assert loaded >= 4
        assert v.reveal("FOO") == "bar"
        assert v.reveal("QUOTED") == "quoted-value"
        assert v.reveal("SINGLE") == "single-value"
        assert v.reveal("WHITESPACE") == "padded"
        assert not v.has("EMPTY")

    def test_load_dotenv_missing_file_is_noop(self, tmp_path: Path):
        v = SecretsVault()
        loaded = v.load_dotenv(tmp_path / "nonexistent.env")
        assert loaded == 0
        assert len(v) == 0
