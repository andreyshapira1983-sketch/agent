"""Does local routing fire when it should? — the recall half of the intent router.

`tests/test_operator_intent.py` asks whether a match is *correct*. This file asks
whether it *happens*. Both halves are needed, and the missing half cost real
money: measured 2026-07-26 over the pattern file's own trigger phrases, **283 of
372 natural phrasings (76%) stopped routing when one neutral word was inserted**
and went to the LLM, which then answered as a generic language model rather than
from the agent's own state. `docs/COGNITIVE_CORE.md` §8.1 carries the numbers.

Three properties are pinned here:

1. **Recall** — the phrases are *derived from the pattern file itself*, so a
   trigger phrase added later is covered automatically, without anyone
   remembering to extend a list.
2. **No over-capture** — a corpus of ordinary turns must stay out of the operator
   command lane. Recall bought with false captures is not a win: it would turn
   "объясни, как работает бюджетный контур" into a command.
3. **The suppression guards stay strict** — `_looks_like_*` decides when routing
   is *blocked*. Loosening that is a different and riskier decision than widening
   what is recognised, so it is asserted at the source level.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path

import core.operator_intent_patterns as patterns
from core.operator_intent import route_operator_intent

PATTERN_SOURCE = Path(patterns.__file__).read_text(encoding="utf-8")
_CYRILLIC = re.compile(r"[Ѐ-ӿ]")
#: Neutral words an operator drops into a question without changing its meaning.
FILLERS = ("уже", "сейчас", "теперь")


def _declared_phrases() -> dict[str, str]:
    """Trigger phrases the pattern file declares, that route on their own.

    Derived, not typed: every Russian phrase of three or more words found in a
    tuple/list literal in the module. Adding a phrase to the module extends this
    test for free.
    """
    found: set[str] = set()
    for node in ast.walk(ast.parse(PATTERN_SOURCE)):
        if isinstance(node, (ast.Tuple, ast.List)):
            for element in node.elts:
                if isinstance(element, ast.Constant) and isinstance(element.value, str):
                    phrase = element.value.strip()
                    if _CYRILLIC.search(phrase) and len(phrase.split()) >= 3:
                        found.add(phrase)
    routed = {}
    for phrase in sorted(found):
        intent = route_operator_intent(phrase)
        if intent is not None:
            routed[phrase] = intent.kind
    return routed


def _variants(phrase: str) -> list[str]:
    """The same phrase with one filler wedged in at a plausible position."""
    words = phrase.split()
    out = []
    for position in sorted({1, len(words) - 1}):
        for filler in FILLERS:
            out.append(" ".join([*words[:position], filler, *words[position:]]))
    return out


# ── 1. recall ────────────────────────────────────────────────────────────────

def test_the_corpus_is_derived_and_not_empty():
    """Guard the guard: a broken extractor must not make this file vacuous."""
    routed = _declared_phrases()
    assert len(routed) >= 50, f"phrase extraction collapsed: {len(routed)}"
    assert len(set(routed.values())) >= 10, "too few intents represented"


def test_an_inserted_filler_never_costs_a_local_route():
    """The measured defect, as an assertion: 76% miss must stay at 0%."""
    routed = _declared_phrases()
    lost = []
    for phrase, kind in routed.items():
        for variant in _variants(phrase):
            intent = route_operator_intent(variant)
            got = intent.kind if intent is not None else "-> LLM"
            if got != kind:
                lost.append(f"{variant!r}: {kind} -> {got}")
    assert not lost, (
        f"{len(lost)} phrasings lost their local route (a turn sent to the LLM "
        f"answers as a generic model instead of from the agent's own state):\n  "
        + "\n  ".join(lost[:15])
    )
