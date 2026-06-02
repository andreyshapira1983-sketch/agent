"""§3.x Prompt Registry — centralised tracking of all LLM system prompts.

Prompts self-register at module-load time via `register_prompt()`.
Consumers retrieve via `get_prompt(key)`.

Override any prompt without code changes by setting an env var:
    AGENT_PROMPT_<KEY_UPPER>
where the key is uppercased and dots/hyphens replaced by underscores.
Example: key "synthesizer.system" → AGENT_PROMPT_SYNTHESIZER_SYSTEM
"""

from __future__ import annotations

import hashlib
import os
from dataclasses import dataclass


# ── Data ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class PromptRecord:
    """Immutable record for a registered prompt."""

    key: str          # e.g. "synthesizer.system"
    content: str      # the full prompt text
    module: str       # originating module, e.g. "core.loop"
    version: str      # SHA-256 hex digest of content
    description: str  # optional human-readable note


# ── Registry class ────────────────────────────────────────────────────────────


class PromptRegistry:
    """Thread-safe registry of named prompt templates.

    If the same key is registered more than once with *different* content,
    the latest registration wins (content evolution across sessions).
    If the content is identical, the call is a no-op.
    """

    def __init__(self) -> None:
        self._store: dict[str, PromptRecord] = {}

    # ── write ─────────────────────────────────────────────────────────────

    def register(
        self,
        key: str,
        content: str,
        module: str = "",
        description: str = "",
    ) -> PromptRecord:
        """Register *content* under *key*; return the resulting record."""
        version = hashlib.sha256(content.encode("utf-8")).hexdigest()
        record = PromptRecord(
            key=key,
            content=content,
            module=module,
            version=version,
            description=description,
        )
        self._store[key] = record
        return record

    # ── read ──────────────────────────────────────────────────────────────

    def get(self, key: str) -> str:
        """Return prompt text for *key*.

        Checks for an env-var override first:
            AGENT_PROMPT_<KEY_UPPER>  (dots/hyphens → underscores)
        Raises ``KeyError`` if key not found and no env override exists.
        """
        env_name = "AGENT_PROMPT_" + key.upper().replace(".", "_").replace("-", "_")
        override = os.environ.get(env_name)
        if override is not None:
            return override

        if key not in self._store:
            known = sorted(self._store)
            raise KeyError(
                f"Prompt key {key!r} is not registered. "
                f"Known keys: {known}"
            )
        return self._store[key].content

    def get_record(self, key: str) -> PromptRecord:
        """Return the full :class:`PromptRecord` (version, module, etc.)."""
        if key not in self._store:
            raise KeyError(f"Prompt key {key!r} is not registered.")
        return self._store[key]

    def get_version(self, key: str) -> str:
        """Return SHA-256 hex digest of the registered content for *key*."""
        return self.get_record(key).version

    def list_keys(self) -> list[str]:
        """Return sorted list of all registered prompt keys."""
        return sorted(self._store)

    # ── dunder helpers ────────────────────────────────────────────────────

    def __contains__(self, key: object) -> bool:
        return key in self._store

    def __len__(self) -> int:
        return len(self._store)


# ── Global singleton + convenience functions ──────────────────────────────────

_REGISTRY = PromptRegistry()


def register_prompt(
    key: str,
    content: str,
    module: str = "",
    description: str = "",
) -> PromptRecord:
    """Register a prompt with the global singleton registry.

    Intended to be called by each owning module once at import time::

        from core.prompt_registry import register_prompt
        MY_PROMPT = "..."
        register_prompt("module.variant", MY_PROMPT, module="core.mymodule")
    """
    return _REGISTRY.register(key, content, module=module, description=description)


def get_prompt(key: str) -> str:
    """Get prompt text from the global registry. Env-var overrides apply.

    Raises ``KeyError`` if the key was never registered and no env override
    is set.
    """
    return _REGISTRY.get(key)


def get_prompt_version(key: str) -> str:
    """Return SHA-256 hex digest of the globally registered prompt content."""
    return _REGISTRY.get_version(key)


def list_prompt_keys() -> list[str]:
    """Return sorted list of all keys in the global registry."""
    return _REGISTRY.list_keys()
