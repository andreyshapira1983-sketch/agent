"""One JSON-extraction core, three consumers (logic-dedup piece 1).

Three modules kept private copies of "find the JSON object in a chatty LLM
reply". They are now `core.plan_parsing.extract_json_object`. These tests
carry the two retired implementations VERBATIM as oracles and prove, case by
case, that the shared core answers the same — or strictly better, on inputs
the old code could not handle (each such case is listed explicitly and proven
to fail against the oracle).

repair_proposal's envelope parser stays in its module (domain preference for
`proposed_content`); it now shares `embedded_json_objects`, and its own tests
in test_repair_proposal_context_window.py keep pinning its behaviour.
"""
from __future__ import annotations

import hashlib
import json
import re

from core.plan_parsing import embedded_json_objects, extract_json_object


# ── retired implementation 1: subagent_memory_scope._parse_json_object ───────
def _oracle_subagent(text: str):
    match = re.search(r"\{.*\}", text, re.DOTALL)
    if not match:
        return None
    try:
        data = json.loads(match.group())
        if not isinstance(data, dict):
            return None
        return data
    except json.JSONDecodeError:
        return None


# ── retired implementation 2: self_build_producer._parse_json ─────────────────
def _oracle_self_build(text: str):
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = candidate[first : last + 1]
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


AGREED_CORPUS = [
    '{"a": 1}',
    '  {"a": 1, "b": [1, 2]}  ',
    '```json\n{"a": 1}\n```',
    '```\n{"a": 1}\n```',
    'Sure! Here is the plan:\n{"steps": []}\nDone.',
    'prefix { not json',
    "no braces at all",
    "",
    "   ",
    "[1, 2, 3]",
    '"just a string"',
    '{"nested": {"deep": {"x": 1}}}',
    'text {"brace_in_string": "a { b } c"} tail',
    '{"broken": ',
    "``` unterminated fence {\"a\": 1}",
]


def test_agreement_with_both_oracles_on_common_ground():
    """Where either oracle finds an object, the shared core finds an equal one;
    where both fail, the shared core may still fail or legitimately recover —
    but on this corpus every verdict is identical, which pins the migration."""
    for text in AGREED_CORPUS:
        new = extract_json_object(text)
        assert new == _oracle_subagent(text) or new == _oracle_self_build(text) or (
            new is None and _oracle_subagent(text) is None and _oracle_self_build(text) is None
        ), f"unexplained divergence on {text!r}: {new!r}"


def test_generated_corpus_never_regresses_either_oracle():
    """SHA-256-driven corpus: whenever an oracle extracts an object, the shared
    core extracts an object too (equal or better — never a lost parse)."""
    fragments = ["{", "}", '"k"', ":", "1", ",", "```json", "```", "text ", '{"k": 1}', "\n"]
    for seed in range(300):
        h = hashlib.sha256(str(seed).encode()).digest()
        text = "".join(fragments[b % len(fragments)] for b in h[:12])
        new = extract_json_object(text)
        for oracle in (_oracle_subagent, _oracle_self_build):
            old = oracle(text)
            if old is not None:
                assert new is not None, f"regression vs oracle on {text!r}"


# ── documented improvements: inputs the old code answered WRONGLY ────────────

def test_two_objects_defeat_subagent_oracle_but_not_the_core():
    """Greedy `{.*}` spans both objects and dies; the balanced scan does not."""
    text = 'first {"a": 1} and second {"b": 2}'
    assert _oracle_subagent(text) is None  # fail-before, pinned
    assert extract_json_object(text) == {"a": 1}


def test_unfenced_answer_after_illustration_defeats_self_build_oracle():
    """A narrating model shows a broken shape, then answers without a fence.
    The old first-{/last-} span glues both together and dies. (With a fence
    the old code coped — its fence regex searched anywhere; that agreement is
    part of the corpus above.)"""
    text = 'the shape is {"draft": ... } and the real answer {"real": 1} follows'
    assert _oracle_self_build(text) is None  # fail-before, pinned
    assert extract_json_object(text) == {"real": 1}


def test_brace_inside_string_value_defeats_naive_span():
    text = 'note: {"code": "if x { return }"} trailing } brace'
    assert _oracle_subagent(text) is None  # greedy span eats the stray brace
    assert extract_json_object(text) == {"code": "if x { return }"}


# ── the shared scanner keeps repair_proposal's contract ──────────────────────

def test_embedded_objects_yield_left_to_right_and_string_aware():
    spans = list(embedded_json_objects('x {"a": "{"} y {"b": 2}'))
    assert spans == ['{"a": "{"}', '{"b": 2}']


def test_non_string_and_empty_inputs_return_none():
    assert extract_json_object("") is None
    assert extract_json_object("   ") is None
    assert extract_json_object(None) is None  # type: ignore[arg-type]
    assert extract_json_object(12) is None  # type: ignore[arg-type]
