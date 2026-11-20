"""Topic words from text, and topic weight: how much a word resolves at all."""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: A word of letters/digits; any other character separates (a comma is not a letter).
_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)

#: Compound name: two or more parts joined by `_` or `.`. Cyrillic is allowed as well as Latin.
_COMPOUND_RE = re.compile(r"[^\W\d_][\w]*(?:[_.][^\W\d_][\w]*)+", re.UNICODE)


#: Polarity: words that flip meaning. Added to the DECISION set separately, straight from the text; `topic_tokens` is untouched because its length threshold is pinned by a test and other consumers read its vocabulary.
#: Dropping such a word makes "movies WITHOUT nicolas cage" its own opposite ("why does the test fail" vs "why does the test NOT fail" matched verbatim while "не" was in the stop list).
_POLARITY_WORDS: frozenset[str] = frozenset({
    "не", "нет", "ни", "без", "кроме", "нельзя",
    "no", "not", "never", "without", "except", "none", "nor", "neither",
})


def topic_tokens(text: str) -> set[str]:
    """Topic words: parts and whole compound names."""
    raw = str(text or "")
    out = {
        token.casefold()
        for token in _WORD_RE.split(raw)
        # Length threshold and digit exception as before: short numeric tokens ("17", "v2") carry signal, short alphabetic ones do not.
        if len(token.strip()) > 2 or any(ch.isdigit() for ch in token)
    }
    out.update(
        whole.casefold()
        for whole in (m.group(0) for m in _COMPOUND_RE.finditer(raw))
        if len(whole) > 3
    )
    return out


#: Words that do not discriminate: they appear in almost any operator text, so a match ONLY on them does not mean kinship. Moved here from `core/memory_policy.py`, which now reads from here: one vocabulary, not two opinions.
STOPWORDS: frozenset[str] = frozenset(
    {
        # English
        "a", "an", "the", "and", "or", "but", "is", "are", "was", "were",
        "be", "to", "of", "in", "on", "at", "for", "with", "as", "by",
        "this", "that", "these", "those", "it", "its", "what", "which",
        "who", "whom", "how", "when", "where", "why", "do", "does", "did",
        "i", "you", "he", "she", "we", "they", "me", "us", "them", "my",
        "your", "their", "our", "from", "about",
        # Russian
        "и", "или", "но", "что", "как", "это", "так", "же",
        "в", "на", "с", "по", "из", "у", "к", "о", "за", "от", "до",
        "ли", "бы", "был", "была", "было", "были", "есть", "быть",
        "мой", "моя", "мое", "мои", "твой", "твоя", "ваш", "наш",
        # Added from a live measurement: these words alone decided matching (17 procedures of 34 on "вот"/"что"/"мне"; 0.400 on "запустить агента" vs "удалить все логи"). The operator's conversational layer is a way of asking, not a topic.
        "вот", "мне", "тебе", "тебя", "вас", "нам", "им", "ему", "ей",
        "себе", "себя", "ты", "вы", "мы", "он", "она", "они", "оно",
        "делать", "сделать", "сделай", "хочу", "надо", "нужно", "можно",
        "давай", "просто", "ещё", "еще", "тоже", "уже", "там", "тут",
        "здесь", "тогда", "если", "чтобы", "потому", "почему", "когда",
        "где", "куда", "кто", "весь", "все", "всё", "этот", "эта", "эти",
        "этим", "того", "том", "та", "те", "один", "одна",
        # EN conversational filler, same role
        "want", "need", "should", "would", "could", "make", "let", "just",
        "also", "there", "here", "then", "if", "because", "some", "any",
        "one", "all", "get", "got", "like", "very", "please",
    }
)


def discriminating_tokens(text: str) -> set[str]:
    """Topic words without the service ones: what a text DIFFERS in, not what it consists of.

        Tokenization is unchanged; the DECISION changes: two texts are related only if they share at least one discriminating word."""
    words = {w.casefold() for w in _WORD_RE.split(str(text or "")) if w}
    return ({t for t in topic_tokens(text) if t not in STOPWORDS}
            | (words & _POLARITY_WORDS))


@dataclass(frozen=True)
class TokenSalience:
    """What a match on a word is worth."""

    weights: Mapping[str, float]
    #: A word absent from the corpus resolves more than any known one: the operator never said it, so it came with this task.
    unseen: float

    def weight(self, token: str) -> float:
        return self.weights.get(token, self.unseen)

    def overlap(self, query: set[str], document: set[str]) -> float:
        return sum(self.weight(token) for token in query & document)


#: No corpus is no reason to change the rules: flat weight, counting by pieces.
FLAT = TokenSalience(weights={}, unseen=1.0)


def build_salience(documents: Iterable[str]) -> TokenSalience:
    """Weight by rarity in the corpus. A document is one utterance, not one word.

        Counted by documents, not occurrences: a word said twenty times in one conversation does not become ordinary."""
    seen: dict[str, int] = {}
    total = 0
    for document in documents:
        total += 1
        for token in topic_tokens(document):
            seen[token] = seen.get(token, 0) + 1
    if not total:
        return FLAT
    # `total + 1` so the weight can never be zero: a word in ALL documents is very common, but zero weight means "does not exist", and a one-episode corpus zeroed everything.
    return TokenSalience(
        weights={token: math.log((total + 1) / count) for token, count in seen.items()},
        unseen=math.log(total + 1),
    )
