"""Four over-blocks the 17.11 reviewer found in the medium fixes; kept closed here."""
from __future__ import annotations


def test_plain_noun_after_number_is_the_same_value() -> None:
    from core.knowledge_pipeline import _values_equivalent as eq

    assert eq("100", "100 requests")
    assert eq("released in 1991", "released in 1991 by guido")
    assert not eq("up by 5", "up by 5%")
    assert not eq("valued at 5", "valued at 5 billion")
    assert not eq("good", "good for nothing")
