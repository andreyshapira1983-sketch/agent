"""BM25 lexical ranking (k1=1.2, b=0.75, Lucene-style IDF) and its tokenizer."""
from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable

from core.topic_tokens import STOPWORDS as _TOPIC_STOPWORDS

#: Shared with core/topic_tokens.py so subsystems agree on stopwords.
_STOPWORDS: frozenset[str] = _TOPIC_STOPWORDS

_TOKEN_RE = re.compile(r"[\w]+", re.UNICODE)


def tokens(text: str) -> set[str]:
    return {t.lower() for t in _TOKEN_RE.findall(text or "") if t.lower() not in _STOPWORDS and len(t) > 1}


def tag_tokens(tags: Iterable[str]) -> set[str]:
    out: set[str] = set()
    for tag in tags or ():
        lowered = str(tag).casefold().strip()
        if not lowered:
            continue
        out.add(lowered)
        out.update(tokens(lowered.replace("-", " ").replace("_", " ")))
    return out


_BM25_K1 = 1.2
_BM25_B = 0.75
_PREFIX_LEN = 4


def term_counts(text: str, tags: Iterable[str]) -> Counter[str]:
    counts = Counter(
        t for t in (w.lower() for w in _TOKEN_RE.findall(text or ""))
        if t not in _STOPWORDS and len(t) > 1
    )
    counts.update(tag_tokens(tags or ()))
    return counts


def _term_frequency(term: str, counts: Counter[str], prefixes: Counter[str]) -> int:
    """Count of the query term in the record; without an exact hit, 1 on a 4-letter prefix match (Russian endings)."""
    exact = counts.get(term, 0)
    if exact or len(term) < _PREFIX_LEN:
        return exact
    return 1 if prefixes.get(term[:_PREFIX_LEN]) else 0


def bm25_scores(q_tokens: set[str], docs: list[Counter[str]]) -> list[float]:
    """BM25 score of each doc against the query; IDF is computed over these docs."""
    n_docs = len(docs)
    if not n_docs or not q_tokens:
        return [0.0] * n_docs
    prefixes = [Counter(t[:_PREFIX_LEN] for t in d if len(t) >= _PREFIX_LEN) for d in docs]
    lengths = [sum(d.values()) for d in docs]
    avgdl = (sum(lengths) / n_docs) or 1.0
    scores = [0.0] * n_docs
    for q in q_tokens:
        row = [_term_frequency(q, d, pre) for d, pre in zip(docs, prefixes, strict=True)]
        df = sum(1 for f in row if f)
        if not df:
            continue
        idf = math.log((n_docs - df + 0.5) / (df + 0.5) + 1.0)
        for i, f in enumerate(row):
            if f:
                norm = 1.0 - _BM25_B + _BM25_B * lengths[i] / avgdl
                scores[i] += idf * f * (_BM25_K1 + 1.0) / (f + _BM25_K1 * norm)
    return scores
