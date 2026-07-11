"""Language-aware term matching for question routing.

Routing heuristics (e.g. "is this a self-repo introspection question?" /
"does this question want an external web lookup?") used to test their term
lists with a raw Python substring: ``term in question.lower()``. That is
fragile, and it is *especially* fragile in Russian, whose rich morphology
(cases, genders, reflexive pronouns, prefixes) makes short substrings collide
constantly. The concrete production failure that motivated this module:

    term  = "сравни с"                 (compare with ...)
    text  = "сравни своё поведение..."  (compare your OWN behaviour ...)

``"сравни с" in "сравни своё"`` is ``True`` because "своё" begins with "с", so
a purely introspective question was mis-classified as an external lookup and a
web_search leaked onto a private question.

Substituting a tidier English example does not fix this — Russian needs real
token boundaries and normalization, not a calque of English rules. This module
provides exactly that:

* ``normalize_text`` — Unicode casefold + Russian ``ё`` -> ``е`` folding so
  "своё"/"свое" and "твоём"/"твоем" compare equal, plus whitespace collapse.
* ``tokenize`` — Unicode-aware word tokens (``\\w+``), so Cyrillic words are
  split on real boundaries (spaces, punctuation, hyphens) rather than by naive
  character search.
* ``any_term_matches`` — matches a term as a *sequence of whole tokens*. Each
  term token must equal a text token, OR (only when the term token is at least
  ``STEM_MIN`` characters) be a prefix of it. The prefix rule is what lets a
  stem such as "репозитор" match "репозитории"/"репозитория"/"репозиторий"
  while the length floor stops a one/two-letter function word such as the
  preposition "с" or "в" from matching the *start* of a longer word like
  "своё" or "весь".

The term lists themselves stay per-language (each list has an English section
and a Russian section); this module is the shared, boundary-correct engine that
evaluates them.
"""
from __future__ import annotations

import re
from functools import lru_cache

# A term token shorter than this must match a whole text token exactly; only
# longer tokens are treated as inflectional stems (prefix match). This is the
# guard that keeps Russian one/two-letter prepositions ("с", "в", "на", "до")
# and English glue words from matching inside a longer word.
STEM_MIN = 4

_WORD_RE = re.compile(r"\w+", re.UNICODE)


def normalize_text(text: str) -> str:
    """Casefold and fold Russian ``ё`` onto ``е`` for stable comparison."""
    return (text or "").casefold().replace("\u0451", "\u0435")  # ё -> е


def tokenize(text: str) -> tuple[str, ...]:
    """Split text into normalized Unicode word tokens."""
    return tuple(_WORD_RE.findall(normalize_text(text)))


@lru_cache(maxsize=4096)
def _tokenize_term(term: str) -> tuple[str, ...]:
    return tuple(_WORD_RE.findall(normalize_text(term)))


def _token_matches(text_token: str, term_token: str) -> bool:
    if text_token == term_token:
        return True
    # Inflectional stem: only allow a prefix match for sufficiently long term
    # tokens, so short function words never match the start of a longer word.
    return len(term_token) >= STEM_MIN and text_token.startswith(term_token)


def term_matches_tokens(text_tokens: tuple[str, ...], term_tokens: tuple[str, ...]) -> bool:
    """True when ``term_tokens`` occur as a consecutive run inside ``text_tokens``."""
    m = len(term_tokens)
    if m == 0:
        return False
    n = len(text_tokens)
    for i in range(n - m + 1):
        if all(_token_matches(text_tokens[i + j], term_tokens[j]) for j in range(m)):
            return True
    return False


def term_matches(text: str, term: str) -> bool:
    """True when ``term`` (one or more tokens) matches within ``text`` on token
    boundaries, tolerating Russian inflection via stem-prefix matching."""
    return term_matches_tokens(tokenize(text), _tokenize_term(term))


def any_term_matches(text: str, terms) -> bool:
    """True when any term in ``terms`` matches ``text`` on token boundaries.

    ``text`` is tokenized once; each term is tokenized (and cached) once.
    """
    text_tokens = tokenize(text)
    for term in terms:
        if term_matches_tokens(text_tokens, _tokenize_term(term)):
            return True
    return False
