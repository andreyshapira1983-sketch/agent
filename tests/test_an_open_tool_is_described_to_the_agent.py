"""Инструмент, которого нет в шапке планировщика, — тоже мёртв, и тоже молча.

Сторож-близнец к `test_a_registered_tool_is_not_silently_dead.py`. Тот ловит
«зарегистрирован, но выбрасывается санитайзером». У него было слепое пятно:
инструмент может быть зарегистрирован, пропущен санитайзером, ВЫЗВАН — и при
этом не описан в подсказке, из которой планировщик узнаёт, что у него есть и
как этим пользоваться.

Замер 2026-09-23, живой и дорогой. `patch_check` зарегистрирован в
`app/bootstrap.py`, осознанно открыт на автономном пути словом оператора
2026-09-22, вызван агентом **96 раз за сутки** — и в `PLANNER_SYSTEM`
отсутствовал ЦЕЛИКОМ: ни имени, ни аргументов, ни формата файла правки, ни
второго вида блока `LINES`. Агент угадывал формат по отказам.

Цена этого пятна: **262 попытки правки, 28 отклонённых, ЗЕЛЁНЫХ НОЛЬ.**
Двадцать из двадцати восьми падений — формат: 11 «the patch did not apply»
(сторона SEARCH не совпала с файлом) и 9 «text outside blocks». А лекарство
от первых одиннадцати лежало в шапке самого инструмента с 2026-09-22: вид
`LINES` не требует копировать текст дословно, и в `tools/patch_check.py`
записана причина его появления — «модель трижды не смогла переписать сигнатуру
символ в символ, хотя видела её, — номера строк она видит без ошибок».

Класс тот же, что у соседнего сторожа, только на один слой выше: «registered
!= told about». Требовать работы инструментом, договор которого не выдан, —
это спрос без обучения, и счёт 262:0 показывает, чем он кончается.
"""
from __future__ import annotations

import pathlib

from core.planner_prompt import PLANNER_SYSTEM

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Инструменты, ОСОЗНАННО не описанные в шапке планировщика, с основанием.
#: Основание обязательно: список без него превращается в «так исторически
#: сложилось», и следующий читатель не отличит решение от недосмотра.
_DELIBERATELY_UNDOCUMENTED: dict[str, str] = {
    "current_time": "часы без аргументов; планировщик зовёт их по имени, "
                    "описывать нечего — ни формата, ни ограничений",
    "memory_bank": "дверь записи в память: её договор живёт в отказах самой "
                   "двери (три вида, происхождение, потолок за процесс), и "
                   "они приходят агенту в тот же ход",
    "memory_recall": "чтение памяти по слову: один строковый аргумент, "
                     "ограничения приходят в ответе самой двери",
    "model_route": "его дверь к политике маршрутов; договор — в отказах двери",
    "model_roster": "чтение своего реестра моделей без аргументов",
}


def _tools_named_in_prompt() -> set[str]:
    """Имена инструментов, описанных в шапке как `- имя(`."""
    names: set[str] = set()
    for line in PLANNER_SYSTEM.split("\n"):
        if line.startswith("- ") and "(" in line:
            head = line[2:line.index("(")].strip()
            if head and head.replace("_", "").isalnum():
                names.add(head)
    return names


def _tools_open_on_the_unattended_path() -> set[str]:
    """Инструменты, осознанно открытые безнадзорному пути."""
    from tests.test_a_new_tool_cannot_slip_onto_the_unattended_path import (
        _DELIBERATELY_OPEN,
    )
    return set(_DELIBERATELY_OPEN)


def test_every_tool_open_to_the_agent_is_described_to_the_agent() -> None:
    """Открыт — значит описан. Иначе это спрос без обучения."""
    open_tools = _tools_open_on_the_unattended_path()
    assert open_tools, "список открытых пуст — сломан сам разбор, а не список"

    described = _tools_named_in_prompt()
    missing = sorted(open_tools - described - set(_DELIBERATELY_UNDOCUMENTED))

    assert not missing, (
        "инструмент открыт агенту и НЕ описан в шапке планировщика: "
        f"{missing}. Он либо не будет выбран вовсе, либо будет вызван "
        "наугад — как patch_check, у которого 262 попытки правки дали ноль "
        "зелёных, потому что формат файла правки агенту не выдавали. "
        "Опиши его в PLANNER_SYSTEM или внеси в _DELIBERATELY_UNDOCUMENTED "
        "С ОСНОВАНИЕМ."
    )


def test_patch_check_carries_its_format(): 
    """У `patch_check` в шапке обязан быть формат файла правки.

    Имени и аргументов недостаточно: отказ приходит словами «text outside
    blocks» и «the patch did not apply», а собрать блок по этим словам
    нельзя. Заперты оба вида блока и запрет текста вне блоков — ровно те три
    вещи, на которых умерли 20 правок из 28.
    """
    i = PLANNER_SYSTEM.find("- patch_check(")
    assert i >= 0, "patch_check не описан в шапке"
    block = PLANNER_SYSTEM[i:i + 2500]
    for needed in ("LINES", "<<<<<<< SEARCH", ">>>>>>> REPLACE", "======="):
        assert needed in block, f"в описании patch_check нет {needed!r}"
    assert "text outside blocks" in block, (
        "в описании нет запрета текста вне блоков — 9 правок из 28 умерли на нём"
    )


def test_the_undocumented_list_names_a_reason() -> None:
    """Список исключений без основания — это «так сложилось», а не решение."""
    for name, reason in _DELIBERATELY_UNDOCUMENTED.items():
        assert len(reason) > 30, f"{name}: основание слишком короткое"


def test_the_undocumented_list_has_no_stale_names() -> None:
    """Инструмент, которого больше нет, не остаётся в списке исключений."""
    open_tools = _tools_open_on_the_unattended_path()
    stale = sorted(set(_DELIBERATELY_UNDOCUMENTED) - open_tools)
    assert not stale, f"в списке исключений имена, которых нет среди открытых: {stale}"
