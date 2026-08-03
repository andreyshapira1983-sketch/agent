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

import pytest

import core.operator_intent_patterns as patterns
from core.operator_intent import route_operator_intent
from core.operator_intent_patterns import _has_any, _has_any_loose, _loose_patterns

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
            out.append(" ".join(words[:position] + [filler] + words[position:]))
    return out


# ── 1. recall ────────────────────────────────────────────────────────────────

def test_the_corpus_is_derived_and_not_empty():
    """Guard the guard: a broken extractor must not make this file vacuous."""
    routed = _declared_phrases()
    assert len(routed) >= 50, f"phrase extraction collapsed: {len(routed)}"
    assert len({kind for kind in routed.values()}) >= 10, "too few intents represented"


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


@pytest.mark.parametrize(
    "phrase,expected",
    [
        ("что ты уже умеешь", "capability_check"),
        ("что вы уже умеете", "capability_check"),
        ("какие у вас есть возможности", "capability_check"),
        ("готов уже к автономной работе", "autonomy_readiness"),
        ("где сейчас слабое место", "weakness_finder"),
        ("what can you already do", "capability_check"),
    ],
)
def test_the_phrasings_from_the_live_session(phrase, expected):
    """Spelled out, because these are the exact shapes that failed in practice."""
    intent = route_operator_intent(phrase)
    assert intent is not None, f"fell through to the LLM: {phrase!r}"
    assert intent.kind == expected


# ── 2. no over-capture ───────────────────────────────────────────────────────

ORDINARY_TURNS = (
    "объясни, как работает бюджетный контур",
    "напиши тест для парсера",
    "что ты думаешь про этот код",
    "почему упал последний прогон",
    "расскажи, как устроена память",
    "покажи мне файл core/loop.py",
    "давай обсудим архитектуру",
    "мне нужно понять, где узкое место в производительности",
    "я хочу добавить новую команду",
    "сколько стоит один прогон",
    "как ты понимаешь мои фразы",
    "можно ли доверять этому ответу",
    "перепиши этот абзац проще",
    "переведи это на английский",
    "какие тесты покрывают маршрутизацию",
    "не надо ничего запускать, просто объясни",
    "в чём разница между этими двумя подходами",
    "предложи план на неделю",
    "что было в прошлой сессии",
    "проверь орфографию в этом тексте",
    "мне кажется, ты меня не понял",
    "верни предыдущий вариант",
    "сделай таблицу из этих данных",
    # both halves of a trigger phrase, far apart, inside an ordinary sentence
    "что ты сделаешь, если я скажу, что ты совсем не умеешь считать",
    "что ты ответишь человеку, который заявляет, будто вы не умеете читать",
)


@pytest.mark.parametrize("turn", ORDINARY_TURNS)
def test_an_ordinary_turn_is_not_hijacked_into_a_command(turn):
    intent = route_operator_intent(turn)
    kind = intent.kind if intent is not None else None
    assert kind is None, f"{turn!r} was captured as {kind}"


# ── 3. the primitive's contract ──────────────────────────────────────────────

def test_loose_matching_tolerates_one_insertion_and_no_more():
    terms = ("готов к автономной работе",)
    assert _has_any_loose("готов к автономной работе", terms)
    assert _has_any_loose("готов уже к автономной работе", terms)
    assert not _has_any_loose("готов вот прямо уже к автономной работе", terms), (
        "two inserted words must not match — looseness may not compound"
    )


def test_single_word_terms_get_no_tolerance():
    assert _loose_patterns("самостро") == ()
    assert _has_any_loose("самострой", ("самостро",)), "substring behaviour is unchanged"


def test_strict_matching_is_untouched():
    assert _has_any("готов к автономной работе", ("готов к автономной работе",))
    assert not _has_any("готов уже к автономной работе", ("готов к автономной работе",))


def test_suppression_guards_keep_strict_matching():
    """`_looks_like_*` decides when routing is BLOCKED — that stays literal.

    Widening what is recognised is one decision; widening what is suppressed is
    another, and a riskier one. This is asserted at the source level because the
    difference is invisible from the outside until it misfires.
    """
    blocks = re.split(r"(?=^def )", PATTERN_SOURCE, flags=re.MULTILINE)
    guards = [b for b in blocks if b.startswith("def _looks_like_")]
    assert len(guards) >= 5, "guard extraction collapsed"
    offenders = [
        b.splitlines()[0] for b in guards if "_has_any_loose(" in b
    ]
    assert not offenders, f"suppression guards must stay strict: {offenders}"


def test_positive_matchers_use_the_loose_primitive():
    """The other half of the same design decision, so it cannot silently revert."""
    blocks = re.split(r"(?=^def )", PATTERN_SOURCE, flags=re.MULTILINE)
    matchers = [b for b in blocks if b.startswith("def _matches_")]
    assert len(matchers) >= 20, "matcher extraction collapsed"
    strict_only = [
        b.splitlines()[0]
        for b in matchers
        if "_has_any(" in b.replace("_has_any_loose(", "")
    ]
    assert not strict_only, (
        "these positive matchers still use strict substring matching, so a filler "
        f"word will send their turns to the LLM: {strict_only}"
    )
