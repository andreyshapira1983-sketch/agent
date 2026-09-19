"""Regression: the episodic tokenizer must keep digit-bearing short tokens.

CORE-10: ``core.smart_memory._tokens`` dropped every token of length <= 2, so
numbers and short alphanumerics ("17", "23", "v2", "3d") vanished from episodic
match keys. "How much is 17 times 23" reduced to {"times"}, and two different
arithmetic questions became indistinguishable. Purely-alphabetic short words
(stopword-like) stay dropped — this fix only recovers digit-bearing signal.
"""
from __future__ import annotations

from core.smart_memory import _tokens


def test_numeric_tokens_are_kept():
    # Was {"times"} before the fix — the numbers were silently dropped.
    assert _tokens("17 times 23") == {"17", "times", "23"}


def test_short_alphanumeric_tokens_are_kept():
    assert "v2" in _tokens("release v2 notes")
    assert "3d" in _tokens("3d render pipeline")


def test_generic_short_words_still_dropped():
    # No stopword regression: purely-alphabetic 1-2 char tokens stay out.
    assert _tokens("is an ok") == set()
