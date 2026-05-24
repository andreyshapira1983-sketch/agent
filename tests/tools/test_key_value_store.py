"""tests/tools/test_key_value_store.py — KV tools"""

import pytest
from tools.builtins.key_value_store import (
    KeyValueStore,
    KeyValueGetTool,
    KeyValueSetTool,
    KeyValueDeleteTool,
)


def _fresh_store():
    return KeyValueStore()


class TestKeyValueStore:
    def test_set_and_get(self):
        s = _fresh_store()
        s.set("key", "value")
        found, val = s.get("key")
        assert found is True
        assert val == "value"

    def test_get_missing(self):
        s = _fresh_store()
        found, val = s.get("ghost")
        assert found is False
        assert val is None

    def test_delete_existing(self):
        s = _fresh_store()
        s.set("x", 1)
        deleted = s.delete("x")
        assert deleted is True
        found, _ = s.get("x")
        assert found is False

    def test_delete_missing(self):
        s = _fresh_store()
        assert s.delete("ghost") is False

    def test_list_keys(self):
        s = _fresh_store()
        s.set("b", 2)
        s.set("a", 1)
        assert s.list_keys() == ["a", "b"]

    def test_clear(self):
        s = _fresh_store()
        s.set("a", 1)
        s.set("b", 2)
        count = s.clear()
        assert count == 2
        assert s.list_keys() == []

    def test_overflow(self):
        from tools.builtins.key_value_store import _MAX_STORE_KEYS
        s = _fresh_store()
        for i in range(_MAX_STORE_KEYS):
            s.set(f"k{i}", i)
        with pytest.raises(OverflowError):
            s.set("overflow", "!")

    def test_overwrite_existing(self):
        s = _fresh_store()
        s.set("x", 1)
        s.set("x", 99)  # overwrite — should not raise overflow
        _, val = s.get("x")
        assert val == 99


class TestKVGetTool:
    def setup_method(self):
        self.store = _fresh_store()
        self.tool = KeyValueGetTool(store=self.store)

    def test_get_existing(self):
        self.store.set("name", "Alice")
        r = self.tool.execute(key="name")
        assert r.success is True
        assert r.output == "Alice"

    def test_get_missing(self):
        r = self.tool.execute(key="missing")
        assert r.success is False
        assert "not found" in r.error

    def test_get_no_key_param(self):
        r = self.tool.execute()
        assert r.success is False

    def test_get_invalid_key(self):
        r = self.tool.execute(key="key with spaces!")
        assert r.success is False


class TestKVSetTool:
    def setup_method(self):
        self.store = _fresh_store()
        self.tool = KeyValueSetTool(store=self.store)

    def test_set_string(self):
        r = self.tool.execute(key="greeting", value="hello")
        assert r.success is True
        _, val = self.store.get("greeting")
        assert val == "hello"

    def test_set_int(self):
        r = self.tool.execute(key="count", value=42)
        assert r.success is True

    def test_set_dict(self):
        r = self.tool.execute(key="data", value={"x": 1, "y": 2})
        assert r.success is True

    def test_set_no_key(self):
        r = self.tool.execute(value="hello")
        assert r.success is False

    def test_set_no_value(self):
        r = self.tool.execute(key="k")
        assert r.success is False
        assert "value" in r.error

    def test_value_too_large(self):
        big = "x" * 70_000
        r = self.tool.execute(key="big", value=big)
        assert r.success is False
        assert "large" in r.error.lower()

    def test_key_with_dot(self):
        r = self.tool.execute(key="user.name", value="Alice")
        assert r.success is True

    def test_key_too_long(self):
        r = self.tool.execute(key="k" * 200, value="v")
        assert r.success is False


class TestKVDeleteTool:
    def setup_method(self):
        self.store = _fresh_store()
        self.tool = KeyValueDeleteTool(store=self.store)

    def test_delete_existing(self):
        self.store.set("tmp", "bye")
        r = self.tool.execute(key="tmp")
        assert r.success is True
        assert r.metadata["deleted"] is True

    def test_delete_missing(self):
        r = self.tool.execute(key="ghost")
        assert r.success is False

    def test_is_destructive_spec(self):
        assert self.tool.spec.is_destructive is True
