"""Всякий путь, пишущий в общий журнал расхода, обязан нести и потолок.

ОБОБЩЕНИЕ MIR-148 со случая на класс. Один такой путь уже нашёлся сверкой
руками: `agent_tick` строил леджер выбора цели своей строкой, без
`budget_ledger`, и пятнадцать оплаченных вызовов остались невидимы для
суточного и недельного потолка. Сверка руками не повторяется сама, поэтому
правило переносится на всякое БУДУЩЕЕ место построения.

Правило узкое нарочно. Оно не запрещает леджер без бюджета вообще: чтение
недавних строк ради сводки (`_provider_health_line`) бюджета не требует и
ничего не тратит. Запрещено ровно одно — построить леджер и отдать его
РОУТЕРУ, потому что роутер тратит деньги.
"""
from __future__ import annotations

import ast
import pathlib

_REPO = pathlib.Path(__file__).resolve().parent.parent

#: Каталоги живого кода. Тесты и полигоны сюда не входят: у пробы своя
#: экономика, и требовать от неё бюджет значило бы требовать конфиг у каждой.
_LIVE = ("core", "cli", "app", "api", "tools")


def _python_files() -> list[pathlib.Path]:
    files = [_REPO / "agent_tick.py", _REPO / "main.py"]
    for name in _LIVE:
        files.extend(sorted((_REPO / name).rglob("*.py")))
    return [f for f in files if f.exists()]


def _ledger_calls_without_budget(tree: ast.AST) -> set[int]:
    """Строки, где ModelUsageLedger(...) строится без `budget_ledger=`."""
    bad: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = ""
        if isinstance(fn, ast.Name):
            name = fn.id
        # ModelUsageLedger.from_env(...)
        elif isinstance(fn, ast.Attribute) and isinstance(fn.value, ast.Name):
            name = fn.value.id
        if name != "ModelUsageLedger":
            continue
        if not any(kw.arg == "budget_ledger" for kw in node.keywords):
            bad.add(node.lineno)
    return bad


def _ledger_handed_to_a_router(tree: ast.AST) -> set[int]:
    """Строки, где такой леджер уходит роутеру — то есть тратит деньги."""
    handed: set[int] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        for kw in node.keywords:
            if kw.arg != "usage_ledger":
                continue
            for inner in ast.walk(kw.value):
                if isinstance(inner, ast.Call):
                    handed.add(inner.lineno)
    return handed


def test_no_router_is_given_a_ledger_that_skips_the_budget() -> None:
    offenders: list[str] = []
    for path in _python_files():
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:  # pragma: no cover - живой код обязан разбираться
            continue
        bad = _ledger_calls_without_budget(tree) & _ledger_handed_to_a_router(tree)
        for line in sorted(bad):
            offenders.append(f"{path.relative_to(_REPO)}:{line}")

    assert not offenders, (
        "роутер получает леджер без бюджета — платные вызовы этого пути "
        "невидимы для суточного и недельного потолка (MIR-148): "
        + ", ".join(offenders)
    )


def test_the_rule_can_see_the_shape_it_forbids() -> None:
    """Контроль: правило обязано уметь показать положительный улов.

    Без него зелёный первый тест значил бы лишь «разбор ничего не нашёл» —
    в том числе если бы разбор был сломан.
    """
    guilty = ast.parse(
        "ModelRouter.from_env(usage_ledger=ModelUsageLedger(p))"
    )
    assert _ledger_calls_without_budget(guilty) & _ledger_handed_to_a_router(guilty)

    innocent = ast.parse(
        "ModelRouter.from_env(usage_ledger=ModelUsageLedger(p, budget_ledger=b))"
    )
    assert not (
        _ledger_calls_without_budget(innocent) & _ledger_handed_to_a_router(innocent)
    )

    allowed = ast.parse("ledger = ModelUsageLedger(p)")
    assert not (
        _ledger_calls_without_budget(allowed) & _ledger_handed_to_a_router(allowed)
    ), "чтение сводки бюджета не требует и запрещаться не должно"
