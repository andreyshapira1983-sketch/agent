"""
tools/builtins/key_value_store.py — In-memory key-value store

Brain uses this as a scratchpad:
    - Store intermediate results during multi-step tasks
    - Share data between tool calls in one reasoning cycle
    - Keep named values across multiple think() calls

This is an in-process store — no persistence between restarts.
For persistent storage, a database adapter would be registered separately.

Security:
    - Keys are validated (alphanumeric + underscores, max 128 chars)
    - Values are stored as-is but size-limited
    - delete is marked is_destructive=True
"""

from __future__ import annotations

import threading
from typing import Any

from ..base import ToolBase, ToolResult, ToolSpec

_MAX_KEY_LEN = 128
_MAX_VALUE_SIZE = 65_536  # 64 KB per value
_MAX_STORE_KEYS = 1000

_KEY_RE = __import__("re").compile(r'^[A-Za-z0-9_\-\.]+$')


class KeyValueStore:
    """Thread-safe in-memory store shared between KV tool instances."""

    def __init__(self) -> None:
        self._data: dict[str, Any] = {}
        self._lock = threading.Lock()

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            if key not in self._data and len(self._data) >= _MAX_STORE_KEYS:
                raise OverflowError(f"Store full ({_MAX_STORE_KEYS} keys max)")
            self._data[key] = value

    def get(self, key: str) -> tuple[bool, Any]:
        with self._lock:
            if key in self._data:
                return True, self._data[key]
            return False, None

    def delete(self, key: str) -> bool:
        with self._lock:
            return self._data.pop(key, _MISSING) is not _MISSING

    def list_keys(self) -> list[str]:
        with self._lock:
            return sorted(self._data.keys())

    def clear(self) -> int:
        with self._lock:
            count = len(self._data)
            self._data.clear()
            return count


_MISSING = object()
_DEFAULT_STORE = KeyValueStore()


class KeyValueGetTool(ToolBase):
    """
    Get a value from the in-memory key-value store.

    params:
        key (str): The key to retrieve.
    """

    def __init__(self, store: KeyValueStore | None = None) -> None:
        self._store = store or _DEFAULT_STORE

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="kv_get",
            description="Get a named value from the agent's in-memory scratchpad",
            parameters={"key": "str — name of the stored value"},
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        key = params.get("key", "")
        err = _validate_key(key)
        if err:
            return self._fail(err)
        found, value = self._store.get(key)
        if not found:
            return self._fail(f"Key '{key}' not found in store")
        return self._ok(output=value, key=key)


class KeyValueSetTool(ToolBase):
    """
    Set a value in the in-memory key-value store.

    params:
        key   (str): The key to set.
        value (any): The value to store.
    """

    def __init__(self, store: KeyValueStore | None = None) -> None:
        self._store = store or _DEFAULT_STORE

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="kv_set",
            description="Store a named value in the agent's in-memory scratchpad",
            parameters={
                "key":   "str — name of the value (alphanumeric, underscore, dash, dot)",
                "value": "any — the value to store",
            },
            requires_approval=False,
            is_destructive=False,
        )

    def execute(self, **params: Any) -> ToolResult:
        key = params.get("key", "")
        err = _validate_key(key)
        if err:
            return self._fail(err)
        if "value" not in params:
            return self._fail("'value' param is required")
        value = params["value"]
        size = len(str(value))
        if size > _MAX_VALUE_SIZE:
            return self._fail(f"Value too large ({size} chars, max {_MAX_VALUE_SIZE})")
        try:
            self._store.set(key, value)
            return self._ok(output=None, key=key, stored=True)
        except OverflowError as exc:
            return self._fail(str(exc))


class KeyValueDeleteTool(ToolBase):
    """
    Delete a value from the in-memory key-value store.

    params:
        key (str): The key to delete.
    """

    def __init__(self, store: KeyValueStore | None = None) -> None:
        self._store = store or _DEFAULT_STORE

    @property
    def spec(self) -> ToolSpec:
        return ToolSpec(
            name="kv_delete",
            description="Delete a named value from the agent's in-memory scratchpad",
            parameters={"key": "str — name of the value to delete"},
            requires_approval=False,
            is_destructive=True,  # Deletes data
        )

    def execute(self, **params: Any) -> ToolResult:
        key = params.get("key", "")
        err = _validate_key(key)
        if err:
            return self._fail(err)
        deleted = self._store.delete(key)
        if not deleted:
            return self._fail(f"Key '{key}' not found — nothing deleted")
        return self._ok(output=None, key=key, deleted=True)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------

def _validate_key(key: str) -> str | None:
    """Return error string or None if key is valid."""
    if not key or not isinstance(key, str):
        return "'key' param is required and must be a string"
    if len(key) > _MAX_KEY_LEN:
        return f"Key too long ({len(key)} chars, max {_MAX_KEY_LEN})"
    if not _KEY_RE.match(key):
        return "Key must contain only: letters, digits, underscore, dash, dot"
    return None
