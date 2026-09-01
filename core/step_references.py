"""Наблюдённое значение доезжает из шага A в аргументы шага B.

Замер 2026-08-31/09-01, повторён четырежды на разных задачах: плана «прочитай
файл и запиши прочитанное» не существует. Аргументы всех шагов фиксируются в
момент планирования, до единого исполнения, поэтому в `file_write` уезжала
строка вроде ``<content read from .agent_drafts/...>`` — сторож заглушек её
честно отвергал, а агент оставался с выводом «перенос данных возможен только
когда содержимое целиком лежит в самом плане».

Цена этой дыры видна не в одном сломанном шаге, а в поведении: не имея права
опереться на прочитанное, модель ВЫНУЖДЕНА сочинять — схему записи, сигнатуру
функции, содержимое файла. Часть того, что выглядело как «выдумывает вместо
того, чтобы посмотреть», была невозможностью посмотреть и записать в одном
плане.

Контракт ссылки, намеренно узкий:

    {{step:<id или order>.output}}

* подставляется ТОЛЬКО вывод уже исполненного шага — никаких вычислений,
  срезов и выражений: это транспорт, а не язык;
* ссылка разрешается перед исполнением шага-получателя, и только если шаг-
  источник уже дал результат; неразрешённая ссылка не «дорисовывается»
  правдоподобным текстом, а остаётся ошибкой с названной причиной;
* значение подставляется как есть; когда ссылка занимает весь аргумент,
  сохраняется исходный ТИП значения (список остаётся списком), потому что
  превращение результата в строку — это уже догадка о том, что нужно.
"""
from __future__ import annotations

import re
from typing import Any

#: {{step:<ref>.output}} — единственная разрешённая форма.
_REFERENCE_RE = re.compile(
    r"\{\{\s*step:(?P<ref>[A-Za-z0-9_\-]+)\.output\s*\}\}"
)


class UnresolvedStepReference(ValueError):
    """Ссылка на шаг, результата которого нет.

    Отдельный тип, потому что это НЕ ошибка инструмента: шаг-получатель
    вообще не должен исполняться, и причина обязана дойти до журнала целой —
    иначе на его месте появится очередная правдоподобная выдумка.
    """


def has_step_reference(value: Any) -> bool:
    """Есть ли в аргументах хоть одна ссылка на другой шаг."""
    if isinstance(value, str):
        return bool(_REFERENCE_RE.search(value))
    if isinstance(value, dict):
        return any(has_step_reference(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_step_reference(v) for v in value)
    return False


def referenced_steps(value: Any) -> tuple[str, ...]:
    """Все шаги, на которые ссылаются аргументы, в порядке появления."""
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for match in _REFERENCE_RE.finditer(node):
                ref = match.group("ref")
                if ref not in found:
                    found.append(ref)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return tuple(found)


def _resolve_string(text: str, outputs: dict[str, Any]) -> Any:
    match = _REFERENCE_RE.fullmatch(text.strip())
    if match is not None:
        # Ссылка занимает весь аргумент: возвращаем значение КАК ЕСТЬ, чтобы
        # список остался списком, а число числом.
        ref = match.group("ref")
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        return outputs[ref]

    def _substitute(m: re.Match[str]) -> str:
        ref = m.group("ref")
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        return str(outputs[ref])

    return _REFERENCE_RE.sub(_substitute, text)


def resolve_step_references(value: Any, outputs: dict[str, Any]) -> Any:
    """Подставить результаты шагов в аргументы.

    `outputs` — отображение «идентификатор или порядковый номер шага ->
    его вывод». Значения не копируются и не преобразуются: транспорт обязан
    доставить ровно то, что было измерено.
    """
    if isinstance(value, str):
        return _resolve_string(value, outputs)
    if isinstance(value, dict):
        return {k: resolve_step_references(v, outputs) for k, v in value.items()}
    if isinstance(value, list):
        return [resolve_step_references(v, outputs) for v in value]
    if isinstance(value, tuple):
        return tuple(resolve_step_references(v, outputs) for v in value)
    return value
