"""Чтение и запись в разных предложениях — не повод отказаться от работы.

Ночь 2026-09-24: первый цикл кампании и сообщение Claude агенту кончились
английской заготовкой «the request mixes reading and changing over several
paths». В обоих текстах чтение и запись стояли в разных предложениях: книга
читается, конспект или файл правки пишется. Корни: правило смотрело на всю
просьбу разом; «remove()» из описания алгоритма читалось как приказ «удали»;
«запиши» не было среди глаголов записи.
"""
from __future__ import annotations

from core.completion_contract import derive_completion_contract

#: Живой текст цели кампании 23.09 22:41 UTC (сокращён только хвост).
_CAMPAIGN = (
    "В knowledge_library/cs/txt/Morin_OpenDataStructures_Python.txt найти раздел про структуру "
    "данных RandomQueue (или ArrayQueue/ArrayDeque) и выписать точную реализацию операции remove() "
    "для RandomQueue: как выбирается случайный индекс, как выполняется замена элемента последним и "
    "уменьшение размера. Затем проверить расчётом в python_probe: реализовать эту операцию и "
    "убедиться, что каждый элемент удаляется с вероятностью 1/n. Итог запиши файлом "
    "data/notes/20260923T224137_competence_cs.md: источник, дословная цитата строкой с «>», и "
    "проверка с числом."
)


def _targets(text: str) -> list[tuple[str, str]]:
    c = derive_completion_contract(text)
    assert not c.ambiguities, c.ambiguities
    return [(o.deliverable, o.target) for o in c.obligations]


def test_the_campaign_note_task_owes_the_note_not_the_book() -> None:
    """Ломалось здесь: отказ «mixes reading and changing», цикл впустую."""
    assert _targets(_CAMPAIGN) == [
        ("file_exists", "data/notes/20260923T224137_competence_cs.md")]


def test_a_method_name_is_not_an_order() -> None:
    """Ломалось здесь: «remove()» давало долг изменить файл, о котором только спросили."""
    assert derive_completion_contract(
        "Объясни, как работает remove() в core/queue.py").obligations == ()


def test_read_and_write_in_one_sentence_still_asks() -> None:
    """Ломка наоборот: где не различить, какой файл менять, — вопрос, как прежде."""
    assert derive_completion_contract("прочитай core/a.py и исправь core/b.py").ambiguities
