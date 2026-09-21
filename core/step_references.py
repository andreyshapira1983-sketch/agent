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
  превращение результата в строку — это уже догадка о том, что нужно;
* форма ссылки в ПРОЗЕ — ссылкой не является. Замер 2026-09-05 (экзамен,
  ход 22): в objective субагента стояло «ссылок вида {{step:N.output}}»,
  резолвер принял «N» за шаг, и шаг, которому нужно было лишь РАССКАЗАТЬ о
  синтаксисе, провалился целиком. Когда известны шаги плана, ссылкой
  считается номер, идентификатор `step_…` и всё, что план или измерение
  называют шагом; остальное — текст и остаётся текстом. Висячая ссылка на
  номер (`{{step:9.output}}` без девятого шага) по-прежнему ошибка.
"""
from __future__ import annotations

import re
from collections.abc import Collection
from typing import Any

#: {{step:<ref>.output}} — единственная разрешённая форма.
_REFERENCE_RE = re.compile(
    r"\{\{\s*step:(?P<ref>[A-Za-z0-9_\-]+)\.output\s*\}\}"
)

#: {{step:<ref>.output.<поле>}} / {{step:<ref>.output[0]}} — ссылка с путём
#: внутрь вывода. Замер 2026-09-05 (экзамен, ход 34): планировщик написал
#: `{{step:1.output.live_trace_id}}`, форма не совпала с контрактом и уехала
#: в `read_logs` буквальной строкой; инструмент ответил PermissionError про
#: небезопасное имя файла, и агент два хода объяснял не ту ошибку. Такая
#: форма — ссылка, которую нельзя разрешить, а не текст: она распознаётся и
#: остаётся ошибкой с названным правилом.
_FIELD_PATH_RE = re.compile(
    r"\{\{\s*step:(?P<ref>[A-Za-z0-9_\-]+)\.output(?P<path>[.\[][^{}]*?)\s*\}\}"
)


class UnresolvedStepReference(ValueError):
    """Ссылка на шаг, результата которого нет.

    Отдельный тип, потому что это НЕ ошибка инструмента: шаг-получатель
    вообще не должен исполняться, и причина обязана дойти до журнала целой —
    иначе на его месте появится очередная правдоподобная выдумка.
    """


def has_step_reference(value: Any) -> bool:
    """Есть ли в аргументах хоть одна ссылка на другой шаг.

    Ссылка с путём внутрь вывода (`.output.<поле>`) тоже считается: такой
    шаг должен дойти до резолвера и провалиться там с названным правилом,
    а не исполниться с буквальной строкой в аргументах.
    """
    if isinstance(value, str):
        return bool(_REFERENCE_RE.search(value) or _FIELD_PATH_RE.search(value))
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


def _is_reference(
    ref: str, outputs: dict[str, Any], plan_steps: Collection[str] | None,
) -> bool:
    """Ссылка это или проза, упоминающая форму ссылки (см. докстринг модуля).

    Без `plan_steps` (вызовы, не знающие плана) каждая форма — ссылка: это
    старое поведение, и оно остаётся строгим.
    """
    if plan_steps is None or ref in outputs or ref in plan_steps:
        return True
    return ref.isdigit() or ref.startswith("step_")


def _reject_field_path(text: str) -> None:
    """Ссылка вида `{{step:N.output.<поле>}}` — ошибка с названным правилом."""
    match = _FIELD_PATH_RE.search(text)
    if match is None:
        return
    ref, path = match.group("ref"), match.group("path").strip()
    raise UnresolvedStepReference(
        f"step reference {match.group(0).strip()} is not a supported form: "
        f"a path into the output ({path!r}) cannot be resolved; the only "
        f"allowed form is {{{{step:{ref}.output}}}} (the whole output)"
    )


def _delivered(ref: str, output: Any) -> Any:
    """Что везёт ссылка: результат шага, а у команды — её stdout.

    Замер 2026-09-19 (внешний экзамен): лаборатория верно посчитала сумму, а
    `file_write(content={{step:1.output}})` получил словарь {code, stdout,
    exit_code, …} и упал «content must be a string, got dict»; попытки сгорали,
    и в файле оставалась старая сумма. У команды (shell_exec, python_probe)
    результат — это её вывод. Упавшая, прерванная или обрезанная команда
    результатом не является: ссылка не разрешается и называет причину, иначе
    в файл молча ушла бы пустая строка или обрывок.
    """
    if not (isinstance(output, dict) and "stdout" in output and "exit_code" in output):
        return output
    reason = (
        f"named workspace files it was not given {output['missing_inputs']} "
        "(list them in inputs)" if output.get("missing_inputs")
        else "timed out" if output.get("timed_out")
        else f"exited with {output.get('exit_code')}" if output.get("exit_code") != 0
        else "its stdout was truncated" if output.get("stdout_truncated")
        else ""
    )
    if reason:
        tail = str(output.get("stderr") or "").strip()[-200:]
        raise UnresolvedStepReference(
            f"step reference {{{{step:{ref}.output}}}}: the command {reason}, so its "
            f"output is not a result" + (f" (stderr: {tail})" if tail else "")
        )
    return output.get("stdout")


#: Аргументы, где вклеенный внутрь вывод становится адресом. След
#: trace_965c1af5… (ночь 20→21.09): в `math_study/library/txt/{{step:3.output}}`
#: подставился весь вывод шага — кусок чужого файла, десяток имён через перевод
#: строки — и file_read искал такой «файл». Адрес — одно имя.
_ADDRESS_KEYS = frozenset({"path", "url"})


def _one_line(ref: str, value: Any) -> str:
    text = value.strip() if isinstance(value, str) else None
    if text is None or "\n" in text or "\r" in text:
        kind = "multi-line" if text is not None else type(value).__name__
        raise UnresolvedStepReference(
            f"step reference {{{{step:{ref}.output}}}} is pasted inside an address "
            f"(path/url), but its result is {kind}; an address takes one name — "
            f"pick the name first, then reference that step"
        )
    return text


def _resolve_string(
    text: str, outputs: dict[str, Any], plan_steps: Collection[str] | None,
    address: bool = False,
) -> Any:
    _reject_field_path(text)
    match = _REFERENCE_RE.fullmatch(text.strip())
    if match is not None and _is_reference(match.group("ref"), outputs, plan_steps):
        # Ссылка занимает весь аргумент: возвращаем значение КАК ЕСТЬ, чтобы
        # список остался списком, а число числом.
        ref = match.group("ref")
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        return _delivered(ref, outputs[ref])

    def _substitute(m: re.Match[str]) -> str:
        ref = m.group("ref")
        if not _is_reference(ref, outputs, plan_steps):
            return m.group(0)
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        value = _delivered(ref, outputs[ref])
        return _one_line(ref, value) if address else str(value)

    return _REFERENCE_RE.sub(_substitute, text)


def resolve_step_references(
    value: Any,
    outputs: dict[str, Any],
    *,
    plan_steps: Collection[str] | None = None,
) -> Any:
    """Подставить результаты шагов в аргументы.

    `outputs` — отображение «идентификатор или порядковый номер шага ->
    его вывод». Значения не копируются и не преобразуются: транспорт обязан
    доставить ровно то, что было измерено. `plan_steps` — идентификаторы и
    номера ВСЕХ шагов плана; с ними форма ссылки, не называющая ни шага, ни
    номера, читается как проза и не трогается.
    """
    if isinstance(value, str):
        return _resolve_string(value, outputs, plan_steps)
    if isinstance(value, dict):
        return {
            k: (_resolve_string(v, outputs, plan_steps, address=True)
                if k in _ADDRESS_KEYS and isinstance(v, str)
                else resolve_step_references(v, outputs, plan_steps=plan_steps))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [resolve_step_references(v, outputs, plan_steps=plan_steps) for v in value]
    if isinstance(value, tuple):
        return tuple(
            resolve_step_references(v, outputs, plan_steps=plan_steps) for v in value
        )
    return value


def renumber_step_references(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Переписать `{{step:N.output}}` под новый порядок шагов.

    2026-09-21, живой разговор: агент спланировал «прочитать final (1),
    прочитать дополнение (2), записать final = {{step:1.output}} …». Маршрутизатор
    документов (`core/doc_routing._ensure_*_docs_first`) после планировщика
    ВСТАВИЛ в начало чтение доктрины самопочинки — шаги сдвинулись, ссылка
    осталась, и в предложение агента записалась доктрина. Номер ссылки — это
    место шага в плане ПЛАНИРОВЩИКА; при перестановке он обязан ехать за шагом.

    Шаги сопоставляются по тождеству объекта. Ссылка на шаг, которого больше
    нет, не трогается — разрешение ссылки откажет вслух.
    """
    old_to_new: dict[str, str] = {}
    positions = {id(src): i for i, src in enumerate(after, start=1)}
    for old, src in enumerate(before, start=1):
        new = positions.get(id(src))
        if new is not None and new != old:
            old_to_new[str(old)] = str(new)
    if not old_to_new:
        return after

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            return _REFERENCE_RE.sub(
                lambda m: m.group(0).replace(m.group("ref"), old_to_new[m.group("ref")], 1)
                if m.group("ref") in old_to_new else m.group(0), value)
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value

    for src in after:
        if isinstance(src.get("arguments"), dict):
            src["arguments"] = fix(src["arguments"])
    return after
