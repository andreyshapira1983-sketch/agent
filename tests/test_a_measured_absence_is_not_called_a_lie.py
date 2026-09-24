"""Верное утверждение об отсутствии не клеймится «противоречит» и «проверить нельзя».

Ночь 24→25.09, ответы агента оператору (Н-3г):
* «В `core/` нет класса `LLMClient` — единственный клиент называется `LLM`»
  получило absence_refuted_by_evidence словом `llm` — из второй части, которая
  `LLM` как раз называет (trace_ad09b21e…);
* «В tools/ нет ни одного вхождения requests.post» по отчёту поиска «no
  matches for 'requests.post' in 30 text files under tools» получило «проверить
  нельзя» — хотя поиск прошёл всю названную область.
"""
from __future__ import annotations

from types import SimpleNamespace

from core.verifier_absence import absence_certified_by_search, absence_subjects

_SEARCH = SimpleNamespace(
    obtained_via="find_in_files",
    excerpt=("no matches in 30 text files under tools (name=*)\n<<Q>>\n"
             "no matches for 'requests.post' in 30 text files under tools (name=*)"),
)


def test_what_a_clause_names_is_not_what_is_denied() -> None:
    claim = "- В `core/` нет класса `LLMClient` — единственный клиент называется `LLM` [tool:find_in_files]."
    assert "llm" not in absence_subjects(claim)
    assert "llmclient" in absence_subjects(claim)


def test_a_full_search_of_the_named_place_certifies_the_absence() -> None:
    claim = "В tools/ нет ни одного вхождения requests.post — ни один штатный инструмент не ходит в модель напрямую."
    assert absence_certified_by_search(claim, [_SEARCH])


def test_another_place_or_another_word_is_not_certified() -> None:
    assert not absence_certified_by_search("В core/ нет ни одного вхождения requests.post.", [_SEARCH])
    assert not absence_certified_by_search("В tools/ нет ни одного вхождения httpx.", [_SEARCH])
    excerpt_only = SimpleNamespace(obtained_via="file_read", excerpt=_SEARCH.excerpt)
    claim = "В tools/ нет ни одного вхождения requests.post."
    assert not absence_certified_by_search(claim, [excerpt_only]), "выдержка файла отсутствия не доказывает"
