"""Символ из репозитория — интроспекция без пути и без словаря (MIR-098).

Оставшаяся половина формы починки, предписанной самой записью: «называет ли
текст СИМВОЛ, существующий в репозитории». Живой замер 2026-08-22: «что
делает AutonomousQueueRunReport» — настоящий класс, названный без пути, — не
охранялся, и план мог уйти искать его в публичном интернете.

Структурный факт, не словарь: кандидаты — CamelCase и snake_case-идентификаторы
из текста, членство — в AST-индексе классов/функций core/ (кэшируется на
процесс). Выдуманное имя НЕ совпадает — иначе любая верблюжья абракадабра
глушила бы веб. Внешний замысел («сравни X с AutoGen») по-прежнему побеждает.
"""
from __future__ import annotations

from core.doc_routing import _is_self_repo_introspection_question
from core.workspace_reference import repo_symbols_named


def test_a_real_class_named_without_a_path_is_recognized() -> None:
    """Красный свидетель: ровно проба из записи."""
    found = repo_symbols_named("что делает AutonomousQueueRunReport?")

    assert "AutonomousQueueRunReport" in found


def test_a_real_snake_function_is_recognized() -> None:
    found = repo_symbols_named("проверь run_maintenance_pass на живом хранилище")

    assert "run_maintenance_pass" in found


def test_an_invented_name_is_not_a_symbol() -> None:
    """Членство в репозитории обязательно: выдумка не глушит веб."""
    assert repo_symbols_named("что такое SomeMadeUpClassNameXyz?") == []
    assert repo_symbols_named("обычный вопрос без идентификаторов") == []


def test_the_entrys_own_probe_is_now_guarded() -> None:
    assert _is_self_repo_introspection_question(
        "что делает AutonomousQueueRunReport?") is True


def test_external_intent_still_wins() -> None:
    """Отрицательная стража стоит: сравнение с внешним — законный веб."""
    assert _is_self_repo_introspection_question(
        "сравни AutonomousQueueRunReport с подходом AutoGen") is False
