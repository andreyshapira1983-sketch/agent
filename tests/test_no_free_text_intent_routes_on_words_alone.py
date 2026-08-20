"""Operator ruling 2026-08-20: lexis may be a sensor, never the judge.

    No unstructured human-language utterance may acquire operational intent
    solely from a lexical match.
    Explicit machine/governance syntax may remain deterministic.

A colon-prefixed token is machine syntax and keeps its deterministic parser —
a stop or a kill must not be a probability. Everything else arrives as human
language, and a keyword table may only PROPOSE: the model then sees the
original text plus the candidate and may veto it.

Why this ruling was made without proving the old routes wrong: their
correctness is genuinely UNKNOWN, and that is the point. The property being
removed is "the operator must phrase a request close enough to the
developer's vocabulary for the right part of the brain to see it at all" —
which cannot be fixed by lengthening the table, because no table enumerates
a language.

The evidence that the veto is worth extending is not precision but
divergence: on live traffic the lexical route fired 10 times and the model
overturned 3 of them. Lexical candidate and semantic judgement demonstrably
disagree in production, so the gate is load-bearing rather than decorative.

Extending it is also monotone-safe: `_model_says_conversation` returns False
on any uncertainty — model error, unparseable answer, no model at all — so
the deterministic route survives every failure, and the worst case equals
today's behaviour.
"""
from __future__ import annotations

import pathlib
import re

_REPO = pathlib.Path(__file__).resolve().parents[1]


def _declared_kinds() -> set[str]:
    kinds: set[str] = set()
    for rel in ("core/operator_intent_patterns.py", "core/operator_intent.py"):
        text = (_REPO / rel).read_text(encoding="utf-8")
        kinds |= set(re.findall(r'kind="([a-z_]+)"', text))
    return kinds


def _verified_kinds() -> set[str]:
    from cli.intent_bridge import _UNGATED_MACHINE_SYNTAX, _VERIFY_INTENTS

    # The ruling's own exception: machine syntax with no operational effect.
    return set(_VERIFY_INTENTS) | set(_UNGATED_MACHINE_SYNTAX)


def test_every_free_text_intent_is_semantically_adjudicated() -> None:
    ungated = _declared_kinds() - _verified_kinds()
    assert not ungated, (
        "these intent kinds still take a free-text utterance to a local route "
        f"on a keyword match alone: {sorted(ungated)}"
    )


def test_the_gate_is_not_empty_and_names_real_kinds() -> None:
    """Boundary pin: the set must be a subset of what actually exists, or the
    test above could be satisfied by listing imaginary names."""
    declared = _declared_kinds()
    assert declared, "no intent kinds found — the scan broke, not the code"
    assert _verified_kinds() <= declared, (
        f"the gate names kinds that do not exist: "
        f"{sorted(_verified_kinds() - declared)}"
    )
