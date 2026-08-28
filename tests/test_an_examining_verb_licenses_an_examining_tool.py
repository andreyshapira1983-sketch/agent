"""Глагол осмотра оправдывает инструмент осмотра — сенсор не кричит зря.

Замер и границы: MIR-015 в docs/audit/MASTER_ISSUE_REGISTRY.md.

Живой счёт 2026-08-19: 190 срабатываний на 268 ходах (71 %), и 241 из 255
обвинений «unjustified» — промахи СЛОВАРЯ по инструментам, которые таблица
знает: `list_dir` требовал буквального «list files»/«каталог» (114 промахов),
`file_read` цеплялся за "read " с пробелом (44). Планировщик же говорит
«inspect the source», «изучу файл», «посмотрю структуру».

Это чистка сенсора, НЕ навешивание принуждения: дефолт контракта эскалации
(F1 поля 0.65–0.77 — принуждение не строится) стоит как стоял. Смысл чистки —
ниже по течению: обвинения сенсора кормят канал причинных наблюдений
(19 из 27 живых), из которого орган MIR-096 будет строить гипотезы; шумный
свидетель — мусорные гипотезы.
"""
from __future__ import annotations

from core.reasoning_action_check import check_reasoning_actions


def test_the_two_measured_miss_families_no_longer_accuse() -> None:
    """Красный свидетель: живые формулировки промахов 114+44."""
    en = check_reasoning_actions(
        "I will inspect the relevant source files and examine the structure.",
        ["list_dir"],
    )
    ru = check_reasoning_actions(
        "Изучу файл конфигурации и посмотрю, что в нём задано.",
        ["file_read"],
    )

    assert not en.unjustified_actions, en.unjustified_actions
    assert not ru.unjustified_actions, ru.unjustified_actions


def test_a_real_mismatch_is_still_caught() -> None:
    """Контроль: чистка словаря не глушит настоящие расхождения."""
    report = check_reasoning_actions(
        "Посчитаю это в уме, ничего запускать не нужно.",
        ["web_search"],
    )

    assert "web_search" in report.unjustified_actions


def test_a_generic_examining_verb_does_not_advocate_extra_tools() -> None:
    """Обратное направление не вооружается: «изучу» не требует list_dir в плане."""
    report = check_reasoning_actions(
        "Изучу вопрос и отвечу по памяти.",
        [],
    )

    assert not report.mentioned_but_not_planned
