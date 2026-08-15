"""Из текста — тема, и вес темы: насколько слово вообще что-то разрешает.

Подбор процедур считал совпадения штуками. Все слова стоили одинаково, поэтому
запись, делившая с запросом «где», «это» и «все», обходила запись про тот же
самый сигнал. Живой замер 2026-08-15: лучшее совпадение — три общих слова из
тридцати одной записи, то есть выбор решал перевес в одно слово.

Здесь две части одной работы.

РЕЗКА. Составное имя (`evidence_budget_trim`, `loop.py`) выдаётся И по частям,
И целиком. Части нужны, чтобы поймать родство; целое — потому что запись,
содержащая всё имя, знает предмет, а запись со словом `evidence` — не знает.

ВЕС. Слово весит тем больше, чем реже оператор его говорит. Корпус — эпизодная
память, то есть речь самого оператора, а не список служебных слов и не сами
процедуры: по тридцати одной записи `это` и `где` выходят РЕДКИМИ (5 из 31), и
взвешивание по ним не даёт ничего. По двумстам эпизодам они обыденны, а имя
сигнала — нет.

Без корпуса вес плоский, и подбор считает штуками, как раньше: пустая память не
должна менять правила, по которым её читают.

Чем мерялось и что отвергнуто: docs/CODE_NOTES.md, «Resolving power».
"""
from __future__ import annotations

import math
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

#: Слово из букв/цифр. Разделяет любой другой знак — запятая не буква.
_WORD_RE = re.compile(r"[\W_]+", re.UNICODE)

#: Составное имя: две и более части через `_` или `.`. Кириллица допущена
#: наравне с латиницей — оператор пишет и так, и так.
_COMPOUND_RE = re.compile(r"[^\W\d_][\w]*(?:[_.][^\W\d_][\w]*)+", re.UNICODE)


def topic_tokens(text: str) -> set[str]:
    """Слова темы: части и составные имена целиком."""
    raw = str(text or "")
    out = {
        token.casefold()
        for token in _WORD_RE.split(raw)
        # Порог длины и оговорка про цифры — прежние (CORE-10): короткие
        # числовые токены («17», «v2») несут сигнал, короткие буквенные нет.
        if len(token.strip()) > 2 or any(ch.isdigit() for ch in token)
    }
    out.update(
        whole.casefold()
        for whole in (m.group(0) for m in _COMPOUND_RE.finditer(raw))
        if len(whole) > 3
    )
    return out


@dataclass(frozen=True)
class TokenSalience:
    """Сколько стоит совпадение по слову."""

    weights: Mapping[str, float]
    #: Слово, которого в корпусе нет вовсе, разрешает сильнее всех известных:
    #: оператор его никогда не произносил, значит оно пришло с этой задачей.
    unseen: float

    def weight(self, token: str) -> float:
        return self.weights.get(token, self.unseen)

    def overlap(self, query: set[str], document: set[str]) -> float:
        return sum(self.weight(token) for token in query & document)


#: Отсутствие корпуса — не повод менять правила: вес плоский, счёт штучный.
FLAT = TokenSalience(weights={}, unseen=1.0)


def build_salience(documents: Iterable[str]) -> TokenSalience:
    """Вес по редкости в корпусе. Документ — одна реплика, не одно слово.

    Считается по документам, а не по вхождениям: слово, двадцать раз сказанное
    в одном разговоре, обыденным от этого не становится.
    """
    seen: dict[str, int] = {}
    total = 0
    for document in documents:
        total += 1
        for token in topic_tokens(document):
            seen[token] = seen.get(token, 0) + 1
    if not total:
        return FLAT
    # `total + 1`, чтобы вес не мог стать нулём. Слово, встреченное во ВСЕХ
    # документах, обыденно донельзя, но нулевой вес — это не «мало значит», а
    # «не существует»: корпус из одного эпизода обнулял всё подряд, и подбор
    # переставал находить хоть что-нибудь. Пусть весит мало, но весит.
    return TokenSalience(
        weights={token: math.log((total + 1) / count) for token, count in seen.items()},
        unseen=math.log(total + 1),
    )
