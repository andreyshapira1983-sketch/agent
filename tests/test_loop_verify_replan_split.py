"""Раскол `core/loop.py`, кусок 11 — проверка и перепланирование по цитатам.

Второй участок, переезжающий не дословно, а под объявленной подстановкой
(первым был цикл попыток, `tests/test_loop_attempt_split.py`). Причина та же:
21 run-локаль против `max-args = 12` в `ruff.toml` этого же репозитория.

Сверка устроена так же и имеет ту же силу, что дословная: берём исторический
блок, применяем ТУ ЖЕ подстановку `имя -> st.имя` своим кодом и требуем
совпадения AST. Правка сверх подстановки роняет тест; молчаливое расширение
подстановки — тоже, потому что список сверяется с полями датакласса.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_verify_replan as verify_mod
from core.loop import AgentLoop
from core.loop_verify_replan import VerifyState

_REPO = Path(__file__).resolve().parents[1]

#: Ровно те имена, что стали полями состояния. Дублирует датакласс намеренно:
#: это вторая независимая запись преобразования.
SUBSTITUTED = frozenset({
    "_cp", "_disagreement_shadow", "_task_planner_llm", "answer", "artifacts",
    "attempt", "chain", "draft_answer", "failure_history", "file_hint", "goal",
    "may_knowledge", "may_source_registry", "plan", "planner_history",
    "planner_out", "replan_exhausted", "source_ranking", "source_registry",
    "user_question", "verifier_failure",
})

HOLDER = "st"
METHOD = "_verify_and_settle_answer"
STATE_VAR = "_verify_state"


def _history(rev: str = "HEAD") -> str:
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


def _the_block(fn: ast.AST) -> ast.If | None:
    """Единственный `if self.verifier_enabled:` верхнего уровня в теле."""
    found = [
        st for st in fn.body
        if isinstance(st, ast.If) and ast.unparse(st.test) == "self.verifier_enabled"
    ]
    return found[0] if len(found) == 1 else None


#: Начало того, что перенёс ИМЕННО этот кусок. Всё выше в блоке — вызов
#: `_verify_draft`, уехавший раньше, куском 5; в истории на его месте ещё
#: лежит проверка целиком. Сверять её тут второй раз нечего: за неё отвечает
#: `tests/test_loop_verification_split.py`, и граница названа здесь, чтобы
#: пропуск был объявленным, а не подразумеваемым.
_OWNED_FROM = "verify_replan_attempt = 0"


def _owned(block: ast.If) -> list[ast.stmt]:
    """Часть блока, за которую отвечает этот кусок: срез тела плюс `else`."""
    for i, stmt in enumerate(block.body):
        if ast.unparse(stmt) == _OWNED_FROM:
            return block.body[i:] + block.orelse
    raise AssertionError(f"в блоке нет опоры {_OWNED_FROM!r} — границу надо пересмотреть")


class _Substitute(ast.NodeTransformer):
    """`имя` -> `st.имя` для объявленных полей. Ровно это и есть перенос."""

    def visit_Name(self, node: ast.Name) -> ast.AST:
        if node.id in SUBSTITUTED:
            return ast.copy_location(
                ast.Attribute(
                    value=ast.Name(id=HOLDER, ctx=ast.Load()),
                    attr=node.id,
                    ctx=node.ctx,
                ),
                node,
            )
        return node


def test_the_block_moved_under_one_declared_substitution():
    """История + объявленная подстановка = то, что лежит в новом модуле."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_block = _the_block(old_run_inner)
    if old_block is None:  # pragma: no cover — история уже без блока
        pytest.skip("блок в истории не найден — раскол уже зафиксирован")

    new_method = _func(
        ast.parse(Path(verify_mod.__file__).read_text(encoding="utf-8")), METHOD
    )
    assert new_method is not None, f"`{METHOD}` пропал из нового модуля"
    new_block = _the_block(new_method)
    assert new_block is not None, "в новом методе должен быть ровно один такой `if`"

    def _dump(stmts: list[ast.stmt]) -> str:
        return "".join(ast.dump(s, include_attributes=False) for s in stmts)

    expected = [
        ast.fix_missing_locations(_Substitute().visit(ast.parse(ast.unparse(s))))
        for s in _owned(old_block)
    ]
    got = [ast.parse(ast.unparse(s)) for s in _owned(new_block)]
    assert "".join(ast.dump(m) for m in expected) == "".join(ast.dump(m) for m in got), (
        "тело отличается от истории СВЕРХ объявленной подстановки — это уже не перенос"
    )


def test_the_prefix_is_the_earlier_piece_and_nothing_else():
    """Пропущенная часть блока — ровно вызов куска 5, а не тихая правка.

    Предыдущий тест не сверяет начало блока: в истории там ещё лежит проверка
    целиком, потому что кусок 5 вынес её раньше. Без этой проверки «пропустим
    начало» стало бы дырой, через которую в блок можно дописать что угодно.
    """
    new_method = _func(
        ast.parse(Path(verify_mod.__file__).read_text(encoding="utf-8")), METHOD
    )
    assert new_method is not None
    block = _the_block(new_method)
    assert block is not None
    prefix = [
        s for s in block.body
        if ast.unparse(s) != _OWNED_FROM
    ][: len(block.body) - len(_owned(block)) + len(block.orelse)]
    kinds = [type(s).__name__ for s in prefix]
    assert kinds == ["ImportFrom", "ImportFrom", "Assign"], (
        f"начало блока перестало быть «два импорта и вызов куска 5»: {kinds}"
    )
    assert "self._verify_draft(" in ast.unparse(prefix[-1]), (
        "начало блока больше не вызывает вынесенную куском 5 проверку"
    )


def test_the_substitution_matches_the_state():
    """Список подстановки и поля датакласса — одно и то же, в обе стороны."""
    declared = {f.name for f in dataclass_fields(VerifyState)}
    assert declared == set(SUBSTITUTED), (
        "поля состояния разошлись со списком подстановки: "
        f"только в датаклассе {sorted(declared - set(SUBSTITUTED))}, "
        f"только в списке {sorted(set(SUBSTITUTED) - declared)}"
    )


def test_the_body_touches_no_run_local_outside_the_state():
    """В теле не осталось голых run-локалей: всё либо в `st`, либо своё."""
    method = _func(
        ast.parse(Path(verify_mod.__file__).read_text(encoding="utf-8")), METHOD
    )
    assert method is not None
    bound: set[str] = {"self", HOLDER}
    for node in ast.walk(method):
        if isinstance(node, ast.Name) and isinstance(node.ctx, (ast.Store, ast.Del)):
            bound.add(node.id)
        elif isinstance(node, ast.ExceptHandler) and node.name:
            bound.add(node.name)
        elif isinstance(node, (ast.Import, ast.ImportFrom)):
            for alias in node.names:
                bound.add(alias.asname or alias.name.split(".")[0])
    assert not (bound & set(SUBSTITUTED)), (
        "имя поля состояния снова связано как локаль: "
        f"{sorted(bound & set(SUBSTITUTED))} — выход не доедет до вызывающего"
    )


def test_the_caller_unpacks_exactly_the_declared_outputs():
    """Что объявлено выходом — вызывающий обязан забрать, и только это."""
    run_inner = _func(
        ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")), "_run_inner"
    )
    assert run_inner is not None
    unpacked = {
        node.attr for node in ast.walk(run_inner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == STATE_VAR
        and isinstance(node.ctx, ast.Load)
    }
    assert unpacked == set(VerifyState.OUTPUTS), (
        "распаковка разошлась с объявленными выходами: "
        f"не забрали {sorted(set(VerifyState.OUTPUTS) - unpacked)}, "
        f"забрали лишнее {sorted(unpacked - set(VerifyState.OUTPUTS))}"
    )


def test_every_declared_output_is_actually_written():
    """Выход, которому участок ничего не присваивает, — не выход."""
    method = _func(
        ast.parse(Path(verify_mod.__file__).read_text(encoding="utf-8")), METHOD
    )
    assert method is not None
    written = {
        node.attr for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == HOLDER
        and isinstance(node.ctx, ast.Store)
    }
    idle = sorted(set(VerifyState.OUTPUTS) - written)
    assert not idle, f"объявлены выходом, но участок их не пишет: {idle}"


def test_a_broken_verifier_stays_distinguishable_from_no_evidence():
    """`verifier_failure` — про поломку, а не про «не подтвердилось».

    Поле объявлено выходом ровно ради этого различия: ниже по цепочке
    решателей ответ не должен быть наказан за поломку инструмента, который
    его судил. Если поле перестанет уезжать наружу, разница исчезнет молча.
    """
    assert "verifier_failure" in VerifyState.OUTPUTS
    run_inner_src = inspect.getsource(loop_mod.AgentLoop._run_inner)
    assert f"verifier_failure = {STATE_VAR}.verifier_failure" in run_inner_src


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    assert callable(getattr(AgentLoop, METHOD, None))
    assert inspect.getmodule(getattr(AgentLoop, METHOD)) is verify_mod


def test_the_loop_no_longer_defines_it():
    """Дубля нет: участок живёт в одном месте, а не в двух."""
    run_inner = _func(
        ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")), "_run_inner"
    )
    assert run_inner is not None
    assert _the_block(run_inner) is None, "участок остался в `_run_inner`"
