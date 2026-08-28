"""Контракт MIR-140: петля перепланирования НЕ мутирует ответ — пином.

Уточнение оператора (2026-08-28, после сверки с полем): «закрыть оценкой»
допустимо, только если инвариант держит регрессионный тест, а не рассуждение.
Центральный провал поля («чинильщик портит уже правильный ответ, заявленное
принятие растёт, пока правильность падает») к нам не применим ПО ПОСТРОЕНИЮ —
и вот это построение приколочено:

- проверяется всегда ИСХОДНЫЙ черновик (`st.draft_answer`) против обогащённой
  цепочки — перепланирование дотаскивает улики, не переписывает ответ;
- в теле петли нет ни одного присваивания в черновик.

Если кто-нибудь когда-нибудь вставит чинильщика в петлю — этот файл красный.
"""
from __future__ import annotations

import ast
import inspect
import textwrap

from core import loop_verify_replan as mod


def _replan_functions() -> list[ast.FunctionDef]:
    tree = ast.parse(textwrap.dedent(inspect.getsource(mod)))
    return [n for n in ast.walk(tree)
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))]


def test_verification_always_takes_the_original_draft() -> None:
    src = inspect.getsource(mod)

    assert "answer=st.draft_answer" in src, (
        "петля обязана проверять ИСХОДНЫЙ черновик — evidence-additive, "
        "не answer-repairing")


def test_nothing_in_the_loop_assigns_into_the_draft() -> None:
    """Ни одного `st.draft_answer = ...` во всём модуле петли."""
    offenders: list[str] = []
    tree = ast.parse(textwrap.dedent(inspect.getsource(mod)))
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, (ast.AugAssign, ast.AnnAssign)):
            targets = [node.target]
        for target in targets:
            if (isinstance(target, ast.Attribute)
                    and target.attr == "draft_answer"):
                offenders.append(f"строка {node.lineno}")

    assert not offenders, (
        f"в петле появился чинильщик ответа — MIR-140 снова открыт: {offenders}")
