"""`added_at` — колонка в журнале, а не поручение что-то добавить.

Четвёртый и пятый ложные отказы гейта контракта за вечер, 2026-09-20,
21:12 и 21:20. Оба вопроса были читательские: сверить два собственных
реестра агента и сказать, в какой из них по устройству записи ложится
факт о нём самом. Ответом дважды пришло встречное «the request mixes
reading and changing over several paths» — и работа не сделана.

Три прошлые заплаты (закавыченная речь, прошедшее время, будущее второго
лица) сюда не достают. Слова-виновники, измеренные по обоим текстам:

    `added_at`              имя колонки: начинается на стебель `add`,
                            а проверка `ed` в конце его не ловит
    `изменение`, `изменения` существительные, предмет разговора
    `add`                   английский глагол, ПРОЦИТИРОВАННЫЙ в разборе
    `создать`               инфинитив в пересказе чужой просьбы

Перечислять формы дальше бессмысленно — их столько же, сколько в языке.
Два правила заменяют перечисление, и каждое проверено на живом отказе:

  * приказ стоит в ФОРМЕ приказа — повелительное наклонение или
    инфинитив, а не существительное и не имя поля;
  * приказ называет свой ПРЕДМЕТ — глагол письма распоряжается файлом
    только в том предложении, где файл назван.

Второе правило было написано раньше первого, проверено на четвёртом
случае, не объяснило его и откачено; пятый случай показал, что нужны оба.
Это записано здесь, чтобы следующий читатель не чинил их по одному.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

#: Из живого вопроса 21:12 — перечисление полей реестра.
_FIELD_NAMES = (
    "Файл data/source_registry.jsonl существует, в нём 2085 строк, и это "
    "реестр внешних источников: каждая запись это kind=source с полями "
    "locator, added_at, last_read_at, evidence_kind=web_search_hit. "
    "Идентификатора sii_f4e85e91ead006cd в нём нет ни разу. Он лежит в "
    "data/self_improvement_issues.jsonl. Открой оба файла и подтверди или "
    "опровергни: сколько записей в каждом и какие там поля."
)

#: Из живого вопроса 21:20 — разбор того, почему прошлый вопрос отбили.
_TALK_ABOUT_TALK = (
    "Ты его отбил встречным уточнением, и причина была не в тебе: в моём "
    "тексте стояло имя поля added_at, гейт прочитал его как английский "
    "глагол add и решил, что я прошу что-то создать. "
    "Ты ответил, что находку надо класть в data/source_registry.jsonl. "
    "Идентификатора sii_f4e85e91ead006cd в нём нет ни разу — он лежит в "
    "data/self_improvement_issues.jsonl. "
    "В этом канале у тебя нет прав на изменение файлов, поэтому здесь "
    "только отчёт словами; сами изменения идут твоим циклом."
)


def test_a_column_name_is_not_a_request_to_create() -> None:
    contract = derive_completion_contract(_FIELD_NAMES)
    assert not contract.needs_clarification, list(contract.ambiguities)
    assert not contract.obligations, [o.target for o in contract.obligations]


def test_talking_about_a_verb_is_not_using_it() -> None:
    contract = derive_completion_contract(_TALK_ABOUT_TALK)
    assert not contract.needs_clarification, list(contract.ambiguities)
    assert not contract.obligations, [o.target for o in contract.obligations]


def test_other_names_of_that_shape_are_read_the_same_way() -> None:
    """Разговор о собственном устройстве весь состоит из таких имён."""
    for name in ("updated_at", "created_at", "write_state", "remove_lock",
                 "delete_stale", "update_file"):
        text = (f"В core/smart_memory.py есть поле {name}. "
                f"Прочитай его и объясни, кто его читает.")
        contract = derive_completion_contract(text)
        assert not contract.obligations, (name, [o.target for o in contract.obligations])


def test_a_noun_is_not_an_order_either() -> None:
    """«Изменение» — предмет разговора, «измени» — распоряжение."""
    said = derive_completion_contract(
        "Изменение core/loop.py обсуждали вчера; сегодня прочитай его и опиши.")
    assert not said.obligations, [o.target for o in said.obligations]


def test_a_plain_verb_still_orders() -> None:
    """Слово речи в форме приказа, у своего предмета, поручением остаётся."""
    contract = derive_completion_contract("Добавь в core/loop.py счётчик циклов.")
    assert [o.target for o in contract.obligations] == ["core/loop.py"]


def test_an_infinitive_beside_its_path_still_orders() -> None:
    contract = derive_completion_contract(
        "Нужно добавить счётчик циклов в core/loop.py.")
    assert [o.target for o in contract.obligations] == ["core/loop.py"]
