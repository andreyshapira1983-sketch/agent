"""R6 (2026-08-13): содержательная задача — не заказ плана и не статус памяти.

Три живых перехвата, все — слепым заходом probe_r1 или сессией d322a875:

  A3: «Сравни probe_r1/notes_a.md и probe_r1/notes_b.md и перечисли все
      содержательные различия.» → :source-review-plan; NO_BANK на
      LOOP_REQUIRED, RESULT=NOT SATISFIED.
  Q2: «Найди фактическое место хранения episodic memory в текущей
      реализации…» → :implementation-plan («реализации» — обстоятельство
      места, не заказ плана).
  Q3: «Проверь реальный конструктор episodic memory store…» →
      :smart-memory (вопрос о КОДЕ, не о состоянии памяти).

Инвариант: императив над конкретными файловыми объектами и вопросы об
устройстве кода идут в цикл; маршрут берёт только явный заказ плана/статуса.
Позитивные контролы — существующие формулировки — остаются маршрутами.
"""
from __future__ import annotations

from core.operator_intent import route_operator_intent


def test_compare_two_files_is_a_task_not_a_review_plan() -> None:
    intent = route_operator_intent(
        "Сравни probe_r1/notes_a.md и probe_r1/notes_b.md и перечисли все "
        "содержательные различия."
    )
    assert intent is None, intent


def test_an_adverbial_realisation_is_not_a_plan_order() -> None:
    intent = route_operator_intent(
        "Найди фактическое место хранения episodic memory в текущей "
        "реализации. Не угадывай по документации. Дай путь и конкретное "
        "evidence из кода/runtime."
    )
    assert intent is None or intent.kind != "implementation_plan", intent


def test_a_question_about_the_constructor_is_not_a_memory_status() -> None:
    intent = route_operator_intent(
        "Проверь реальный конструктор episodic memory store. Можно ли создать "
        "полностью изолированный временный store, не меняя основной durable "
        "store? Ответ только POSSIBLE, IMPOSSIBLE или UNKNOWN и evidence."
    )
    assert intent is None or intent.kind != "smart_memory_status", intent


def test_an_explicit_source_review_order_still_routes() -> None:
    intent = route_operator_intent("Сравни загруженные источники по теме памяти")
    assert intent is not None and intent.kind == "source_review_plan"


def test_an_explicit_implementation_plan_order_still_routes() -> None:
    intent = route_operator_intent(
        "Составь точный план реализации Operator Task Layer"
    )
    assert intent is not None and intent.kind == "implementation_plan"


def test_a_memory_status_question_still_routes() -> None:
    intent = route_operator_intent("покажи smart memory статус")
    assert intent is not None and intent.kind == "smart_memory_status"
