"""«Ты добавил» — рассказ о сделанном, а не требование сделать.

Живой разговор 2026-09-20, 19:36. Тот же читательский вопрос агенту — «что
помешало тебе открыть тело функции» — снова кончился встречным уточнением
«the request mixes reading and changing over several paths». Первую причину
(закавыченную фразу «данных нет») к тому моменту уже починили; виновато
оказалось одно слово из МОЕГО пересказа его же поступка:

    «…назвал её в ответе и ДОБАВИЛ, что без чтения полного тела функции…»

Стебель «добав» стоит в `_CREATE_STEMS`, и прошедшее время его не смущало:
рассказ о том, что агент уже сделал, читался как поручение что-то добавить.

Это будет повторяться ровно настолько, насколько мы с ним разговариваем:
любой разбор его поступков состоит из «ты написал», «ты создал», «ты
добавил». Поэтому лечится не словарём, а грамматикой: глагол в прошедшем
времени (-л/-ла/-ло/-ли, английское -ed) поручения не несёт. Повелительное
(«добавь», «напиши») и инфинитив («нужно добавить») не тронуты — они и есть
просьба.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

_NARRATION = (
    "Ты нашёл функцию _pick_new_module_path в core/incremental_splitter.py, "
    "назвал её в ответе и добавил, что без чтения полного тела не можешь "
    "утверждать. Прочитай трассу logs/trace_b5a6.jsonl и ответь, что помешало."
)


def test_retelling_what_the_agent_did_is_not_a_task() -> None:
    contract = derive_completion_contract(_NARRATION)
    assert not contract.needs_clarification, list(contract.ambiguities)
    assert not contract.obligations, [o.target for o in contract.obligations]


def test_an_imperative_still_asks() -> None:
    contract = derive_completion_contract("Добавь в core/loop.py счётчик циклов.")
    owed = [(o.deliverable, o.target) for o in contract.obligations]
    assert owed, "повелительное наклонение — это просьба"


def test_an_infinitive_still_asks() -> None:
    contract = derive_completion_contract(
        "Нужно добавить счётчик циклов в core/loop.py.")
    assert [o.target for o in contract.obligations], "инфинитив — тоже просьба"


def test_english_past_is_narration_too() -> None:
    contract = derive_completion_contract(
        "You created core/foo.py and added a counter. Read core/loop.py and "
        "tools/web_fetch.py and tell me what blocked you.")
    assert not contract.needs_clarification, list(contract.ambiguities)
