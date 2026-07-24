"""Unit tests for canonical normalization algorithm."""

import pytest

from project_intelligence.db.normalize import canonicalize, content_hash_text, normalized_value_hash


def test_string_whitespace_and_nfc():
    composed = "café"
    decomposed = "cafe\u0301"
    assert normalized_value_hash(composed) == normalized_value_hash(decomposed)
    assert normalized_value_hash("  a \n b  ") == normalized_value_hash("a b")


def test_dict_key_order_irrelevant():
    assert normalized_value_hash({"z": 1, "a": 2}) == normalized_value_hash({"a": 2, "z": 1})


def test_list_order_matters():
    assert normalized_value_hash([1, 2]) != normalized_value_hash([2, 1])


def test_none_vs_empty_string():
    assert normalized_value_hash(None) != normalized_value_hash("")


def test_bool_not_int():
    assert normalized_value_hash(True) != normalized_value_hash(1)


def test_float_not_string_not_int():
    assert normalized_value_hash(1.0) != normalized_value_hash("1")
    assert normalized_value_hash(1.0) != normalized_value_hash("1.0")
    assert normalized_value_hash(1.0) != normalized_value_hash(1)


def test_distinct_floats_distinct_hashes():
    a = 1.0
    b = 1.0000000000000002  # next representable after 1.0 in many cases
    # If Python distinguishes them as floats, hashes must differ
    if a != b:
        assert normalized_value_hash(a) != normalized_value_hash(b)
    c = 2.5
    d = 2.5000000000000004
    if c != d:
        assert normalized_value_hash(c) != normalized_value_hash(d)


def test_key_collision_after_normalization():
    with pytest.raises(ValueError, match="key collision"):
        canonicalize({"a b": 1, "a  b": 2})


def test_content_hash_text():
    h = content_hash_text("hello")
    assert len(h) == 64
    assert h == content_hash_text("hello")


def test_canonicalize_nested():
    c = canonicalize({"b": [1, {"x": "  y  "}], "a": None})
    assert c["t"] == "dict"
    pairs = {k: v for k, v in c["v"]}
    assert pairs["a"] == {"t": "null"}
    assert pairs["b"]["t"] == "list"
    assert pairs["b"]["v"][0] == {"t": "int", "v": 1}
    assert pairs["b"]["v"][1]["t"] == "dict"
    inner = {k: v for k, v in pairs["b"]["v"][1]["v"]}
    assert inner["x"] == {"t": "str", "v": "y"}


def test_canonicalize_rejects_nan():
    with pytest.raises(ValueError):
        canonicalize(float("nan"))
