"""Раскол `core/loop.py`, кусок 7 — хвост прогона уехал дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Здесь уезжает всё между «текст ответа окончателен» и «эпизод
записан»: последняя редакция, теневые сенсоры, запись хода, уплотнение
памяти, профиль и допущения.

Сверх дословности пинятся два порядка, за которые в этом участке платят
дороже, чем строками. Первый: запись эпизода обязана ОСТАТЬСЯ в цикле и
идти после — успех, записанный раньше, оставляет окно, где последующий сбой
роняет ход с уже забаненным успехом. Второй: в эпизод обязан уехать тот же
текст, что и пользователю, то есть уже отредактированный, иначе память
сохранит секрет, который из ответа вырезали.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_run_tail as tail_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

MOVED = ("_finalize_run_tail",)

_SLICE_START = "safe_answer, answer_findings, answer_pii_findings = redact_dlp_text(answer)"
_SLICE_END_MARKER = "self.assumption_store.save_many("


#: Коммит ПЕРЕД расколом — источник истины для сверок ниже.
#:
#: Раньше здесь стоял `HEAD`, и это работало ровно до первого коммита: как
#: только раскол попал в историю, `HEAD:core/loop.py` стал расколотым файлом,
#: сверять стало не с чем, и все проверки дословности ушли в `skip`. Отказ был
#: виден в отчёте, но гарантия исчезла бы навсегда — а именно её этот файл и
#: держит. Ссылка закреплена на конкретный коммит, поэтому переезд остаётся
#: проверяемым и через год.
_BEFORE_THE_SPLIT = "76941e061bdd8f85dcb6bdc7ab283262be349f62"


def _history(rev: str = _BEFORE_THE_SPLIT) -> str:
    # Подавления по месту, а не через per-file-ignores: remote-режим Codacy
    # путевые исключения не читает (#300). Argv фиксирован, shell не поднимается.
    return subprocess.run(  # noqa: S603  # nosec B603 B607
        ["git", "show", f"{rev}:core/loop.py"],  # noqa: S607
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")


def _func(tree: ast.AST, name: str):
    for node in ast.walk(tree):
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == name:
            return node
    return None


def _moved_slice(body: list[ast.stmt]) -> list[ast.stmt]:
    """Срез: от последней редакции до сохранения допущений, включая."""
    start = end = None
    for i, stmt in enumerate(body):
        text = ast.unparse(stmt)
        if start is None and text.startswith(_SLICE_START):
            start = i
        elif start is not None and _SLICE_END_MARKER in text:
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return body[start:end + 1]


def _new_method():
    return _func(ast.parse(Path(tail_mod.__file__).read_text(encoding="utf-8")),
                 "_finalize_run_tail")


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


def test_logic_moved_symbol_for_symbol():
    """Дословность ЛОГИКИ: срез истории совпадает с телом нового метода."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку дословности не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_stmts = _moved_slice(old_run_inner.body)
    if not old_stmts:  # pragma: no cover — история уже без этого участка
        pytest.skip("участок в истории не найден — раскол уже зафиксирован")
    new = _new_method()
    assert new is not None, "`_finalize_run_tail` пропал из нового модуля"
    assert isinstance(new.body[0], ast.Expr), "первым в теле ждём docstring"
    assert isinstance(new.body[-1], ast.Return), "последним в теле ждём `return`"
    assert _dump(old_stmts) == _dump(new.body[1:-1]), (
        "тело хвоста изменилось при переносе — это уже не перенос"
    )


def test_the_loop_no_longer_defines_it():
    """Дубля нет: хвост живёт в одном месте, а не в двух."""
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            defined = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
            assert not (defined & set(MOVED)), (
                f"метод определён дважды: {sorted(defined & set(MOVED))}"
            )
    run_inner = _func(tree, "_run_inner")
    assert run_inner is not None
    assert not _moved_slice(run_inner.body), "вырезанный участок остался в цикле"


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for name in MOVED:
        assert callable(getattr(AgentLoop, name, None)), f"{name} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, name)) is tail_mod


def test_the_episode_is_still_banked_last_in_the_loop():
    """Запись эпизода не уехала и идёт ПОСЛЕ хвоста.

    Успех, записанный раньше конца, оставляет окно: последующий сбой роняет
    ход с уже забаненным успехом, а идемпотентность по `run_id` откажется
    это исправлять. Порядок здесь — не стиль, а гарантия.
    """
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    texts = [ast.unparse(st) for st in run_inner.body]

    def _last(needle: str) -> int:
        hits = [i for i, text in enumerate(texts) if needle in text]
        assert hits, f"не нашли {needle!r} в теле цикла"
        return hits[-1]

    # Берём ПОСЛЕДНЕЕ вхождение: ранние выходы (реплей эпизода, отказ
    # многофайлового разбора) банкуют свой эпизод и возвращаются, до хвоста
    # не доходя. Речь про банковку основного пути.
    assert _last("_finalize_run_tail") < _last("_record_experience_memory"), (
        "эпизод записывается раньше хвоста — окно для ложного успеха вернулось"
    )
    # И он по-прежнему предпоследний по смыслу: после него только очистка.
    new = _new_method()
    assert new is not None
    assert "_record_experience_memory" not in ast.unparse(new), (
        "запись эпизода уехала в хвост — она обязана остаться последней в цикле"
    )


def test_the_redacted_answer_is_what_reaches_the_episode():
    """Наружу отдаётся отредактированный текст, а не входной.

    Если бы метод редактировал «на месте» и ничего не возвращал, вызывающий
    записал бы в эпизод исходную строку — и память сохранила бы секрет,
    который из ответа как раз вырезали.
    """
    new = _new_method()
    assert new is not None
    returns = [n for n in ast.walk(new) if isinstance(n, ast.Return)]
    assert len(returns) == 1
    assert ast.unparse(returns[0]) == "return (answer, verification, weak_chunks)"
    # `answer` внутри переприсваивается отредактированным — это и есть смысл.
    assert any(
        ast.unparse(st) == "answer = safe_answer" for st in ast.walk(new)
        if isinstance(st, ast.Assign)
    ), "редакция перестала доезжать до возвращаемого значения"

    call = [
        n for n in ast.walk(_func(ast.parse(
            Path(loop_mod.__file__).read_text(encoding="utf-8")), "_run_inner"))
        if isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "_finalize_run_tail"
    ]
    assert len(call) == 1, "вызов должен быть ровно один"


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2300, f"core/loop.py снова разбух: {lines} строк"
