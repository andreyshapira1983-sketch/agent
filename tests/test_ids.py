"""ID factory — format + uniqueness.

Used everywhere as a default value for Pydantic `id` fields. A bug here
makes every trace correlation in JSONL meaningless.
"""
from __future__ import annotations

import re

from core.ids import new_id


def test_id_format_is_prefix_underscore_hex():
    # 8 bytes -> 16 lowercase hex chars.
    pat = re.compile(r"^[a-z]+_[0-9a-f]{16}$")
    assert pat.match(new_id("obs"))
    assert pat.match(new_id("plan"))


def test_prefix_is_preserved_verbatim():
    assert new_id("custom_prefix").startswith("custom_prefix_")
    assert new_id("x").startswith("x_")


def test_ids_are_unique_in_a_tight_loop():
    seen = {new_id("t") for _ in range(2000)}
    # 64 bits keeps IDs compact while avoiding realistic trace collisions.
    assert len(seen) == 2000


def test_empty_prefix_still_returns_valid_id():
    # Edge case: callers should pass a non-empty prefix, but the function
    # should not crash on an empty string — it just produces `_<hex>`.
    out = new_id("")
    assert out.startswith("_")
    assert len(out) == 1 + 16
