"""«Сценарий» — не «цена»: фрагмент реального времени — начало слова, не середина.

2026-09-21, разговор через мостик: вопрос про свой код со словами «с тем же
сценарием» (с-ЦЕНА-рием) прочитан как вопрос о цене в реальном времени, и ответ
получил приписку «источники недостаточны для подтверждения realtime-значения
без специализированного live источника с timestamp».
"""
from __future__ import annotations

from core.source_ranker import is_realtime_question


def test_a_word_containing_a_fragment_is_not_realtime() -> None:
    assert not is_realtime_question("Прогони пробой модуль с тем же сценарием, что в тесте.")
    assert not is_realtime_question("Разбери дискурс этой статьи.")


def test_the_fragments_still_fire_at_the_start_of_a_word() -> None:
    assert is_realtime_question("Какая цена биткоина?")
    assert is_realtime_question("Какой текущий курс доллара?")
