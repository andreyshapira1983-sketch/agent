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
import os

import pytest

from core.prompt_registry import (
    PromptRecord,
    PromptRegistry,
    get_prompt,
    get_prompt_version,
    list_prompt_keys,
    register_prompt,
    _REGISTRY,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

_SHA = lambda text: hashlib.sha256(text.encode()).hexdigest()  # noqa: E731


# ─────────────────────────────────────────────────────────────────────────────
# TestPromptRecord
# ─────────────────────────────────────────────────────────────────────────────


class TestPromptRecord:
    def test_fields_stored(self):
        rec = PromptRecord(
            key="a.b", content="hello", module="core.x",
            version="abc", description="desc",
        )
        assert rec.key == "a.b"
        assert rec.content == "hello"
        assert rec.module == "core.x"
        assert rec.version == "abc"
        assert rec.description == "desc"

    def test_frozen(self):
        rec = PromptRecord(key="a", content="c", module="m", version="v", description="")
        with pytest.raises((AttributeError, TypeError)):
            rec.key = "changed"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# TestRegistryInstance
# ─────────────────────────────────────────────────────────────────────────────


class TestRegistryInstance:
    """Uses a fresh PromptRegistry per test to avoid global state."""

    def setup_method(self):
        self.reg = PromptRegistry()

    def test_register_returns_record(self):
        rec = self.reg.register("x.system", "hello", module="core.x")
        assert isinstance(rec, PromptRecord)
        assert rec.key == "x.system"
        assert rec.content == "hello"

    def test_version_is_sha256(self):
        text = "You are a helpful assistant."
        rec = self.reg.register("v.test", text)
        assert rec.version == _SHA(text)

    def test_get_returns_content(self):
        self.reg.register("g.test", "my prompt")
        assert self.reg.get("g.test") == "my prompt"

    def test_get_record_returns_record(self):
        self.reg.register("gr.test", "prompt", module="mod")
        rec = self.reg.get_record("gr.test")
        assert rec.module == "mod"

    def test_get_version_matches_sha(self):
        self.reg.register("gv.test", "abc123")
        assert self.reg.get_version("gv.test") == _SHA("abc123")

    def test_list_keys_sorted(self):
        self.reg.register("b.test", "b")
        self.reg.register("a.test", "a")
        assert self.reg.list_keys() == ["a.test", "b.test"]

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

    def test_re_register_different_content_updates(self):
        self.reg.register("u.test", "old")
        self.reg.register("u.test", "new")
        assert self.reg.get("u.test") == "new"

    def test_description_stored(self):
        self.reg.register("d.test", "x", description="A description")
        assert self.reg.get_record("d.test").description == "A description"


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

    def test_env_override_hyphen_key(self, monkeypatch):
        self.reg.register("env-hyphen", "original")
        monkeypatch.setenv("AGENT_PROMPT_ENV_HYPHEN", "patched")
        assert self.reg.get("env-hyphen") == "patched"

    def test_env_override_unregistered_key(self, monkeypatch):
        """Env var works even for a key that is NOT in the registry."""
        monkeypatch.setenv("AGENT_PROMPT_TOTALLY_NEW_KEY", "injected")
        assert self.reg.get("totally.new.key") == "injected"

    def test_no_env_override_returns_original(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_ENV_TEST", raising=False)
        assert self.reg.get("env.test") == "original"

    def test_empty_env_var_treated_as_override(self, monkeypatch):
        """Empty string env var overrides the registered content."""
        monkeypatch.setenv("AGENT_PROMPT_ENV_TEST", "")
        assert self.reg.get("env.test") == ""


# ─────────────────────────────────────────────────────────────────────────────
# TestUnknownKey
# ─────────────────────────────────────────────────────────────────────────────


class TestUnknownKey:
    def setup_method(self):
        self.reg = PromptRegistry()
        self.reg.register("known.key", "text")

    def test_get_raises_key_error(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_UNKNOWN_KEY", raising=False)
        with pytest.raises(KeyError, match="unknown.key"):
            self.reg.get("unknown.key")

    def test_error_mentions_known_keys(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_UNKNOWN_KEY", raising=False)
        with pytest.raises(KeyError, match="known.key"):
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
# TestVersionStability
# ─────────────────────────────────────────────────────────────────────────────


class TestVersionStability:
    def test_same_content_same_version(self):
        reg1 = PromptRegistry()
        reg2 = PromptRegistry()
        text = "You are a careful analyst.\n\nPlease be thorough."
        rec1 = reg1.register("s.test", text)
        rec2 = reg2.register("s.test", text)
        assert rec1.version == rec2.version

    def test_different_content_different_version(self):
        reg = PromptRegistry()
        rec1 = reg.register("d.test", "hello")
        reg.register("d.test", "world")
        rec2 = reg.get_record("d.test")
        assert rec1.version != rec2.version

    def test_version_is_64_hex_chars(self):
        reg = PromptRegistry()
        rec = reg.register("vlen.test", "abc")
        assert len(rec.version) == 64
        assert all(c in "0123456789abcdef" for c in rec.version)


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

    def test_register_and_get(self, monkeypatch):
        monkeypatch.delenv("AGENT_PROMPT_TEST_GLOBAL_HELPER", raising=False)
        register_prompt(self._KEY, "global content")
        assert get_prompt(self._KEY) == "global content"

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

    _EXPECTED = {
        "synthesizer.system": "core.loop",
        "planner.system": "core.planner",
        "repair_proposal.system": "core.repair_proposal",
        "subagent_scope.system": "core.subagent_memory_scope",
        "reflection.system": "core.reflection",
    }

    def setup_class(self):
        # Trigger self-registration by importing the owning modules.
        import core.loop  # noqa: F401
        import core.planner  # noqa: F401
        import core.repair_proposal  # noqa: F401
        import core.subagent_memory_scope  # noqa: F401
        import core.reflection  # noqa: F401

    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_key_present(self, key):
        assert key in _REGISTRY, f"Prompt key {key!r} not found in global registry"

    @pytest.mark.parametrize("key,module", list(_EXPECTED.items()))
    def test_module_tag(self, key, module):
        rec = _REGISTRY.get_record(key)
        assert rec.module == module, (
            f"Expected module={module!r} for key={key!r}, got {rec.module!r}"
        )

    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_content_non_empty(self, key):
        content = _REGISTRY.get(key)
        assert len(content) > 50, (
            f"Prompt {key!r} seems empty or too short ({len(content)} chars)"
        )

    @pytest.mark.parametrize("key", list(_EXPECTED))
    def test_version_is_sha256(self, key):
        version = _REGISTRY.get_version(key)
        assert len(version) == 64
        assert all(c in "0123456789abcdef" for c in version)

    def test_all_five_registered(self):
        for key in self._EXPECTED:
            assert key in _REGISTRY
