"""Прочитанное доезжает до записи — иначе модель вынуждена сочинять.

Замер 2026-08-31/09-01, повторён на четырёх разных задачах: плана «прочитай
файл и запиши прочитанное» не существовало. Аргументы всех шагов фиксируются
при планировании, поэтому в `file_write` уезжала строка
``<content read from .agent_drafts/...>`` — сторож заглушек её отвергал, и
агент вывел: «перенос данных возможен только когда содержимое целиком лежит в
самом плане».

Последствие было не в одном сломанном шаге, а в поведении: не имея права
опереться на прочитанное, модель ВЫНУЖДЕНА была угадывать — схему записи,
сигнатуру функции, содержимое файла. Часть того, что выглядело как «выдумывает
вместо того, чтобы посмотреть», оказалась невозможностью посмотреть и записать
в одном плане.

Здесь проверяется транспорт: ссылка `{{step:<id|order>.output}}` делает шаг
зависимым, разрешается ПОСЛЕ измерения источника и никогда не подставляет
правдоподобное вместо измеренного.
"""
from __future__ import annotations

import pytest

from core.placeholder_text import looks_like_unfilled_content
from core.step_references import (
    UnresolvedStepReference,
    has_step_reference,
    referenced_steps,
    resolve_step_references,
)


def test_a_whole_argument_keeps_the_measured_type():
    outputs = {"0": ["первая", "вторая"]}

    resolved = resolve_step_references({"items": "{{step:0.output}}"}, outputs)

    assert resolved["items"] == ["первая", "вторая"], "список обязан остаться списком"


def test_a_reference_inside_text_is_substituted():
    outputs = {"read": "содержимое файла"}

    resolved = resolve_step_references(
        {"content": "начало\n{{step:read.output}}\nконец"}, outputs)

    assert resolved["content"] == "начало\nсодержимое файла\nконец"


def test_references_are_found_at_any_depth():
    arguments = {
        "path": "core/loop.py",
        "payload": {"nested": ["{{step:2.output}}", "плоский текст"]},
        "content": "{{step:read_a.output}}",
    }

    assert has_step_reference(arguments)
    assert referenced_steps(arguments) == ("2", "read_a")


def test_arguments_without_references_are_untouched():
    arguments = {"path": "core/loop.py", "content": "обычный текст"}

    assert not has_step_reference(arguments)
    assert resolve_step_references(arguments, {}) == arguments


def test_a_missing_source_is_an_error_not_a_guess():
    """Главное свойство: неразрешённая ссылка НЕ дорисовывается."""
    with pytest.raises(UnresolvedStepReference) as excinfo:
        resolve_step_references({"content": "{{step:7.output}}"}, {"0": "есть"})

    assert "no result" in str(excinfo.value)
    assert "'0'" in str(excinfo.value), "причина обязана назвать, что было известно"


def test_the_reference_form_is_not_mistaken_for_a_placeholder():
    """Транспорт не воюет со сторожем заглушек: форма ссылки ему не родня."""
    assert not looks_like_unfilled_content("{{step:0.output}}")
    assert looks_like_unfilled_content("<content read from .agent_drafts/x.py>")


def test_an_empty_output_is_still_a_measurement():
    """Пустой вывод — это измеренный факт, а не отсутствие результата."""
    resolved = resolve_step_references({"content": "{{step:0.output}}"}, {"0": ""})

    assert resolved["content"] == ""
