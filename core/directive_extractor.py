"""Turn instruction text into ranked ``Directive`` objects by mapping each sentence to a stance on a known axis."""
from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass

from core.instruction_conflict_gate import AuthorityLevel, Directive


@dataclass(frozen=True)
class Stance:
    """One position on an axis, and the wordings that signal it."""

    demand: str
    patterns: tuple[str, ...]


@dataclass(frozen=True)
class Axis:
    """A decision that can only be settled one way."""

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
                    # A qualifier often sits between the two words, so allow a few intervening tokens in both directions.
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
                    # Negation is part of the requirement here; these patterns consume it themselves (see ``_is_negated``).
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



@dataclass(frozen=True)
class SourceText:
    """A block of instruction text tagged with its origin; authority comes from the origin, never from wording."""

    text: str
    source_level: AuthorityLevel
    source_name: str
    locator: str = ""


# Sentence boundary: . ! ? ; or a newline (bullets in review comments end a sentence without punctuation).
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
    """True when a negation flips the matched phrase."""
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



def extract(sources: Iterable[SourceText]) -> tuple[Directive, ...]:
    """Extract ranked directives from tagged instruction text."""
    found: list[Directive] = []
    seen: set[tuple[str, str, str, str]] = set()

    for source in sources:
        for sentence in _sentences(source.text):
            for axis, stance, pattern in _COMPILED:
                # Every occurrence, not just the first; a negated match is skipped, not flipped to the opposite stance (it negates the wording, not the axis).
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
    """Task statement plus a review comment, the common case, without building ``SourceText`` objects at each call site."""
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
