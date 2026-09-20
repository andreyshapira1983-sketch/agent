"""Искомое слово — вопрос поиска, а не улика его исхода.

Живой разговор 2026-09-20, 19:25. Агента спросили, почему он предложил
вынести функцию в `core/smart_memory_helpers2.py`. Он честно обыскал 3305
файлов и написал правду: такого файла нет, совпадений ноль. Заявку я к тому
времени отклонил, файл не создавался — агент был прав.

Верификатор объявил эти правдивые утверждения ЛОЖЬЮ, трижды:

    absence_refuted_by_evidence
      утв: «Поиск строки `smart_memory_helpers2` … не дал совпадений»
      нашли в улике: smart_memory_helpers2

Разумеется нашли. `tools/find_in_files.py:128` печатает
«no matches for 'smart_memory_helpers2' in 3305 text files», то есть ЧЕСТНЫЙ
отчёт о ненайденном обязан назвать искомое — по построению. Гейт видел имя в
улике и заключал, что отсутствие опровергнуто собственным доказательством.

Лечится тем же различением, что и код пробы (`QUESTION_CODE_MARKER`,
core/evidence.py): имена — из вопроса, истина — из исхода. Отзвук запроса
уезжает за маркер: гейты истины его отрезают (`truth_excerpt`), а гейт
литералов по-прежнему видит, что имя не выдумано.
"""
from __future__ import annotations

from core.evidence import QUESTION_CODE_MARKER, evidence_from_tool_result
from core.verifier_absence import absence_reason, absent_literal_reason
from core.verifier_utils import truth_excerpt

_EMPTY = "no matches for 'smart_memory_helpers2' in 3305 text files under ."
_CLAIM = ("- Поиск строки `smart_memory_helpers2` по всем 3305 текстовым "
          "файлам не дал ни одного совпадения [tool:find_in_files].")


def _evidence(output: str):
    return evidence_from_tool_result(
        tool_name="find_in_files",
        arguments={"query": "smart_memory_helpers2", "path": "."},
        output=output)


def test_the_query_echo_leaves_the_truth_excerpt() -> None:
    ev = _evidence(_EMPTY)
    outcome = truth_excerpt(ev.excerpt)
    assert "no matches" in outcome and "3305" in outcome, "исход остаётся уликой"
    assert "smart_memory_helpers2" not in outcome, "запрос — вопрос, не улика"
    assert QUESTION_CODE_MARKER in ev.excerpt
    assert "smart_memory_helpers2" in ev.excerpt.split(QUESTION_CODE_MARKER, 1)[1]


def test_a_true_absence_is_no_longer_called_a_lie() -> None:
    assert absence_reason(_CLAIM, _evidence(_EMPTY), "tool") is None


def test_the_name_is_still_not_a_fabrication() -> None:
    assert absent_literal_reason(_CLAIM, _evidence(_EMPTY), "tool") is None


def test_a_search_that_found_something_is_untouched() -> None:
    found = ("2 matching lines (2 occurrences) in 1 of 3305 text files under .\n"
             "core/smart_memory.py:11: from core.smart_memory_helpers import (")
    ev = _evidence(found)
    assert ev.excerpt == found, "находка — улика целиком, резать нечего"
    assert QUESTION_CODE_MARKER not in ev.excerpt


def test_an_absence_its_evidence_really_refutes_is_still_caught() -> None:
    found = ("1 matching lines (1 occurrences) in 1 of 3305 text files under .\n"
             "core/smart_memory_helpers2.py:1: \"\"\"Helpers extracted verbatim")
    reason = absence_reason(_CLAIM, _evidence(found), "tool")
    assert reason is not None and reason.code == "absence_refuted_by_evidence"
