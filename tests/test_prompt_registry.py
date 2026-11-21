"""Tests for core/prompt_registry.py — §3.x Prompt Registry.

Classes:
    TestPromptRecord         — immutable dataclass, version = sha256
    TestRegistryInstance     — register / get / get_record / list_keys
    TestEnvVarOverride       — AGENT_PROMPT_<KEY> env-var override
    TestUnknownKey           — KeyError with helpful message
    TestVersionStability     — same content → same version hash
    TestGlobalHelpers        — module-level register_prompt / get_prompt / list_prompt_keys
    TestRegisteredPrompts    — 5 modules self-register their prompts at import
"""

from __future__ import annotations

import hashlib
import re
from typing import ClassVar

import pytest

from core.prompt_registry import (
    _REGISTRY,
    PromptRegistry,
    get_prompt_version,
    list_prompt_keys,
    register_prompt,
)

# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_SHA = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731


# ─────────────────────────────────────────────────────────────────────────────
# TestRegistryInstance
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryInstance:
    """Uses a fresh PromptRegistry per test to avoid global state."""

    def setup_method(self):
        self.reg = PromptRegistry()


    def test_contains(self):
        self.reg.register("c.test", "x")
        assert "c.test" in self.reg
        assert "missing" not in self.reg

    def test_len(self):
        assert len(self.reg) == 0
        self.reg.register("l.test", "x")
        assert len(self.reg) == 1

    def test_re_register_same_content_is_noop(self):
        self.reg.register("r.test", "same")
        self.reg.register("r.test", "same")
        assert len(self.reg) == 1


# ─────────────────────────────────────────────────────────────────────────────
# TestEnvVarOverride
# ─────────────────────────────────────────────────────────────────────────────


class TestEnvVarOverride:
    def setup_method(self):
        self.reg = PromptRegistry()
        self.reg.register("env.test", "original")

    def test_env_override_dot_key(self, monkeypatch):
        monkeypatch.setenv("AGENT_PROMPT_ENV_TEST", "overridden")
        assert self.reg.get("env.test") == "overridden"


# ─────────────────────────────────────────────────────────────────────────────
# TestUnknownKey
# ─────────────────────────────────────────────────────────────────────────────


class TestUnknownKey:
    def setup_method(self):
        self.reg = PromptRegistry()
        self.reg.register("known.key", "text")

    def test_get_raises_key_error(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_UNKNOWN_KEY", raising=False)
        with pytest.raises(KeyError, match=re.escape("unknown.key")):
            self.reg.get("unknown.key")

    def test_error_mentions_known_keys(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_UNKNOWN_KEY", raising=False)
        with pytest.raises(KeyError, match=re.escape("known.key")):
            self.reg.get("unknown.key")

    def test_get_record_raises_key_error(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_MISSING", raising=False)
        with pytest.raises(KeyError):
            self.reg.get_record("missing")

    def test_get_version_raises_key_error(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_GHOST", raising=False)
        with pytest.raises(KeyError):
            self.reg.get_version("ghost")


# ─────────────────────────────────────────────────────────────────────────────
# TestGlobalHelpers
# ─────────────────────────────────────────────────────────────────────────────


class TestGlobalHelpers:
    """Test module-level convenience functions against the global registry."""

    _KEY = "test_global.helper"

    def setup_method(self):
        # Ensure clean state for each test by directly manipulating the singleton
        _REGISTRY._store.pop(self._KEY, None)

    def teardown_method(self):
        _REGISTRY._store.pop(self._KEY, None)


    def test_get_prompt_version(self):
        register_prompt(self._KEY, "abc")
        assert get_prompt_version(self._KEY) == _SHA("abc")

    def test_list_prompt_keys_includes_registered(self):
        register_prompt(self._KEY, "x")
        assert self._KEY in list_prompt_keys()

    def test_list_prompt_keys_sorted(self):
        keys = list_prompt_keys()
        assert keys == sorted(keys)


# ─────────────────────────────────────────────────────────────────────────────
# TestRegisteredPrompts  — integration: modules must self-register at import
# ─────────────────────────────────────────────────────────────────────────────


class TestRegisteredPrompts:
    """Verify the 5 primary prompts auto-register when their modules are imported."""

    _EXPECTED: ClassVar[dict[str, str]] = {
        "synthesizer.system": "core.loop",
        "planner.system": "core.planner_prompt",
        "repair_proposal.system": "core.repair_proposal",
        "subagent_scope.system": "core.subagent_memory_scope",
        "reflection.system": "core.reflection",
    }

    def setup_class(self):
        # Trigger self-registration by importing the owning modules.
        import core.loop
        import core.planner_prompt
        import core.reflection
        import core.repair_proposal
        import core.subagent_memory_scope  # noqa: F401

    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_key_present(self, key):
        assert key in _REGISTRY, f"Prompt key {key!r} not found in global registry"


    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_version_is_sha256(self, key):
        version = _REGISTRY.get_version(key)
        assert len(version) == 64
        assert all(c in "0123456789abcdef" for c in version)
