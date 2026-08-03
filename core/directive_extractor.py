"""Turn instruction *text* into ranked ``Directive`` objects.

``core.instruction_conflict_gate`` can decide whether two requirements
contradict each other, but only if somebody hands it structured requirements.
Nothing did — which made the gate a decider that could never fire. This module
is the missing producer.

The idea that makes deterministic extraction work
-------------------------------------------------

Comparing two sentences for "do they contradict" is hopeless without a model.
Comparing two *stances on a known axis* is trivial.

So requirements are not extracted as free text. Each recognised sentence is
mapped onto an **axis** — a decision that can only be settled one way — and to
one **stance** on that axis. "Сохраняй порядок" and "отсортируй по имени" are
two stances on the axis *порядок элементов*; they conflict because the axis
allows only one. Two sources taking the *same* stance produce the same
``demand`` label and therefore no conflict, however differently they are worded.

The source's authority is not guessed from the text at all — it comes from
*where the text arrived from*, which the caller always knows: a review comment
is an ``advisor``, the task statement is a ``task_contract``, a test is a
``test_expectation``.

Honest boundary
---------------

Recall is bounded by the axis registry: a contradiction on an axis nobody has
described here is invisible to this module, and the gate will let the work
proceed. That is a known, deliberate limit — the registry is meant to grow as
real conflicts are met. It is *not* a general-purpose contradiction detector,
and nothing downstream should present it as one.

Design principles
-----------------
* No LLM calls, no I/O, mutates nothing — deterministic and O(n·axes).
* Precision over recall: a sentence that matches nothing yields no directive,
  because a false conflict blocks real work and teaches the operator to ignore
  the gate.
"""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from core.instruction_conflict_gate import AuthorityLevel, Directive

# ---------------------------------------------------------------------------
# Axes
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Stance:
    """One position on an axis, and the wordings that signal it.

    ``demand`` is the stable label written into the ``Directive``. Two sources
    reaching the same stance share it verbatim, which is what stops "the same
    requirement, said differently" from being reported as a contradiction.
    """

    demand: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Axis:
    """A decision that can only be settled one way.

    Stances on the same axis are mutually exclusive by construction — that is
    the whole claim this module makes, and the reason conflict detection can be
    deterministic.
    """

    subject: str
    stances: tuple[Stance, ...]


#: The recognised axes. Patterns are matched case-insensitively against single
#: sentences, in both Russian and English because the repo is bilingual.
#:
#: Growing this registry is how the gate's recall improves. Each new axis needs
#: stances that are genuinely exclusive — two stances that can both be true at
#: once would manufacture conflicts out of compatible requirements.
AXES: tuple[Axis, ...] = (
    Axis(
        subject="порядок элементов",
        stances=(
            Stance(
                demand="сохранять стабильный порядок",
                patterns=(
                    # A qualifier usually sits between the two words
                    # ("порядок вывода должен быть стабильным"), so allow a few
                    # intervening tokens in both directions.
                    r"стабильн\w*\s+(?:\w+\s+){0,2}порядок",
                    r"порядок\w*\s+(?:\w+\s+){0,3}стабильн",
                    r"не\s+мен\w+\s+(?:\w+\s+){0,2}порядок",
                    r"сохран\w+\s+(?:\w+\s+){0,2}порядок",
                    r"stable\s+order",
                    r"order\s+(?:\w+\s+){0,3}stable",
                    r"deterministic\s+order",
                    r"preserve\s+(?:the\s+)?order",
                ),
            ),
            Stance(
                demand="сортировать",
                patterns=(
                    r"отсортир\w*",
                    r"сортир\w*",
                    r"по\s+алфавит\w*",
                    r"sort(?:ed)?\s+by\b",
                    r"alphabetical\w*",
                ),
            ),
        ),
    ),
    Axis(
        subject="сетевые вызовы",
        stances=(
            Stance(
                demand="сетевые вызовы запрещены",
                patterns=(
                    r"без\s+сет\w*",
                    r"сет\w*\s+(?:вызов\w*\s+)?запрещ\w*",
                    r"запрещ\w*\s+\w*\s*сет\w*",
                    r"не\s+(?:делать|использовать)\s+\w*\s*(?:сетев\w*|запрос\w*)",
                    r"offline\b",
                    r"no\s+network",
                    r"without\s+network",
                    r"must\s+not\s+call\s+(?:the\s+)?(?:network|api)",
                ),
            ),
            Stance(
                demand="получать данные по сети",
                patterns=(
                    r"через\s+api\b",
                    r"по\s+api\b",
                    r"запрос\w*\s+к\s+api\b",
                    r"http\s*-?\s*запрос\w*",
                    r"скача\w+",
                    r"fetch\s+(?:it\s+)?(?:from|via)\b",
                    r"call\s+the\s+api\b",
                ),
            ),
        ),
    ),
    Axis(
        subject="обработка исключений",
        stances=(
            Stance(
                demand="исключение пробрасывается наружу",
                patterns=(
                    r"пробрас\w*",
                    r"не\s+глуш\w*",
                    r"не\s+подавл\w*",
                    r"исключени\w*\s+\w*\s*(?:должн\w*\s+)?(?:подним\w*|наружу)",
                    r"re-?raise",
                    r"must\s+propagate",
                    r"let\s+it\s+raise",
                ),
            ),
            Stance(
                demand="исключение подавляется",
                patterns=(
                    r"проглот\w*",
                    r"подави\w*",
                    r"замолч\w*",
                    r"пойма\w+\s+\w*\s*(?:и\s+)?верн\w+",
                    r"swallow\s+(?:the\s+)?(?:error|exception)",
                    r"catch\s+(?:it\s+)?and\s+return",
                    r"except\s*:\s*pass",
                ),
            ),
        ),
    ),
    Axis(
        subject="изменение существующих файлов",
        stances=(
            Stance(
                demand="существующие файлы не изменять",
                patterns=(
                    r"не\s+мен\w+\s+\w*\s*существующ\w*",
                    r"не\s+трог\w+\s+\w*\s*существующ\w*",
                    r"существующ\w*\s+файл\w*\s+не\s+(?:мен\w+|измен\w+|трог\w+)",
                    r"только\s+нов\w+\s+файл\w*",
                    r"do\s+not\s+(?:modify|touch|edit)\s+existing",
                    r"new\s+files\s+only",
                ),
            ),
            Stance(
                demand="изменить существующий файл",
                patterns=(
                    r"зарегистрир\w*",
                    r"добав\w+\s+в\s+(?:индекс|реестр|список|карт\w*)",
                    r"обнов\w+\s+(?:индекс|реестр|карт\w*)",
                    r"register\s+(?:the\s+)?(?:module|it)\b",
                    r"add\s+(?:it\s+)?to\s+the\s+(?:index|registry|map)",
                ),
            ),
        ),
    ),
    Axis(
        subject="изменение тестов",
        stances=(
            Stance(
                demand="тесты не изменять",
                patterns=(
                    # The negation is part of the requirement here, so these
                    # patterns consume it themselves — see ``_is_negated``.
                    r"не\s+(?:мен\w+|измен\w+|прав\w+|трог\w+)\s+\w*\s*тест\w*",
                    r"тест\w*\s+не\s+(?:мен\w+|измен\w+|прав\w+|трог\w+)",
                    r"do\s+not\s+(?:change|modify|touch)\s+the\s+tests?",
                    r"tests?\s+must\s+not\s+change",
                ),
            ),
            Stance(
                demand="изменить тест",
                patterns=(
                    r"(?:поправ\w+|измен\w+|перепиш\w+|обнов\w+)\s+\w*\s*тест\w*",
                    r"ослаб\w+\s+\w*\s*(?:тест\w*|проверк\w*)",
                    r"удал\w+\s+\w*\s*(?:тест\w*|assert\w*)",
                    r"(?:update|relax|delete|drop)\s+the\s+(?:test|assertion)",
                ),
            ),
        ),
    ),
)


# Pre-compiled once at import: (axis, stance, compiled pattern).
_COMPILED: tuple[tuple[Axis, Stance, re.Pattern[str]], ...] = tuple(
    (axis, stance, re.compile(pattern, re.IGNORECASE))
    for axis in AXES
    for stance in axis.stances
    for pattern in stance.patterns
)


def known_subjects() -> tuple[str, ...]:
    """The axes this extractor can see. Anything else is invisible to it."""
    return tuple(axis.subject for axis in AXES)


# ---------------------------------------------------------------------------
# Input
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class SourceText:
    """A block of instruction text, tagged with where it came from.

    The authority level is structural — the caller knows whether this text is a
    review comment or the task statement — so it is never inferred from wording.
    """

    text: str
    source_level: AuthorityLevel
    source_name: str
    locator: str = ""


# Sentence boundary: ".", "!", "?", ";" or a newline. Bullet lists are common in
# review comments, so a line break ends a sentence even without punctuation.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?;])\s+|\n+")


#: Words that invert the requirement that follows them. "Не сортируй" is not a
#: request to sort, and reading it as one manufactures a conflict out of
#: agreement — the most damaging kind of false positive this module can make.
_NEGATIONS = frozenset({
    "не", "нет", "нельзя", "никогда", "без",
    "not", "no", "never", "don't", "dont", "avoid", "without",
})

#: How many words before a match are searched for a negation. Three covers
#: "не надо это сортировать" without reaching back into the previous clause.
_NEGATION_LOOKBEHIND_WORDS = 3

_WORD = re.compile(r"[\w']+", re.UNICODE)

#: Russian puts some negations *after* the verb — "сортировать не надо". Only a
#: few fixed forms do this, and the window deliberately does not cross
#: punctuation: a negation after a comma belongs to the next clause
#: ("отсортируй по имени, не надо ничего усложнять" is still a sort request).
_POST_NEGATION = re.compile(
    r"^(?:\s+[\w']+){0,2}\s+(?:не\s+(?:надо|нужно|стоит|требуется|следует)|нельзя)\b",
    re.IGNORECASE | re.UNICODE,
)


def _is_negated(sentence: str, match_start: int, match_end: int) -> bool:
    """True when a negation flips the matched phrase.

    Patterns that spell out their own negation ("не менять порядок") consume the
    negation themselves, so nothing precedes the match and this correctly
    returns False.
    """
    preceding = _WORD.findall(sentence[:match_start].lower())
    if any(
        word in _NEGATIONS
        for word in preceding[-_NEGATION_LOOKBEHIND_WORDS:]
    ):
        return True
    return _POST_NEGATION.search(sentence[match_end:]) is not None


def _sentences(text: str) -> tuple[str, ...]:
    if not isinstance(text, str) or not text.strip():
        return ()
    parts = (part.strip(" \t-*•–—") for part in _SENTENCE_SPLIT.split(text))
    return tuple(" ".join(part.split()) for part in parts if part.strip())


# ---------------------------------------------------------------------------
# Extraction
# ---------------------------------------------------------------------------

def extract(sources: Iterable[SourceText]) -> tuple[Directive, ...]:
    """Extract ranked directives from tagged instruction text.

    One directive per (source, axis, stance): a source that repeats the same
    stance in three sentences states one requirement, not three. The first
    matching sentence is kept as the verbatim quote, so the operator sees the
    source's own words in the conflict report.
    """
    found: list[Directive] = []
    seen: set[tuple[str, str, str, str]] = set()

    for source in sources:
        for sentence in _sentences(source.text):
            for axis, stance, pattern in _COMPILED:
                # Every occurrence, not just the first: in "не сортируй по
                # имени, сортируй по дате" the opening match is negated while
                # the second is a real demand, and stopping at the first would
                # hide it. A negated occurrence is skipped rather than flipped
                # to the opposite stance — it negates the wording, not the
                # axis, and guessing which was meant would be the same
                # confident invention this module exists to avoid.
                affirmed = any(
                    not _is_negated(sentence, m.start(), m.end())
                    for m in pattern.finditer(sentence)
                )
                if not affirmed:
                    continue
                key = (
                    source.source_level,
                    source.source_name,
                    axis.subject,
                    stance.demand,
                )
                if key in seen:
                    continue
                seen.add(key)
                found.append(Directive(
                    source_level=source.source_level,
                    source_name=source.source_name,
                    subject=axis.subject,
                    demand=stance.demand,
                    locator=source.locator,
                    quote=sentence,
                ))
    return tuple(found)


def extract_from_task_and_review(
    *,
    task_text: str = "",
    review_text: str = "",
    task_source: str = "постановка задачи",
    review_source: str = "код-ревью",
    task_locator: str = "",
    review_locator: str = "",
) -> tuple[Directive, ...]:
    """The common shape: the task statement plus a review comment.

    Exists so the frequent case does not require assembling ``SourceText``
    objects at every call site.
    """
    return extract((
        SourceText(
            text=task_text,
            source_level="task_contract",
            source_name=task_source,
            locator=task_locator,
        ),
        SourceText(
            text=review_text,
            source_level="advisor",
            source_name=review_source,
            locator=review_locator,
        ),
    ))
