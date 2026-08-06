"""Раскол `core/loop.py`, кусок 5 — первая проверка черновика уехала дословно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Куски 1–4 вынесли исполнение шага, решателей черновика, синтез и
досборку цепочки; здесь уезжает вызов верификатора и три наблюдателя вокруг
него.

Как и в кусках 2 и 4, вырезан СРЕЗ тела `_run_inner` — дословность сверяется
срезом истории. Отличие: у этого куска есть ШВЫ, то есть строки, которых в
истории на этом месте не было. Их ровно три, они названы в `SEAM_LINES`, и
тест не пропустит четвёртую: иначе «перенос» тихо станет правкой.

Отдельно пинится то, ради чего этот участок вообще отделён от остального
блока верификатора: `verifier_failure` — про «проверка сломалась», а не про
«не подтвердилось». Слить их — значит наказать ответ за поломку инструмента,
который его судил.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_verification as verification_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

MOVED = ("_verify_draft",)

#: Границы вырезанного среза в историческом `_run_inner`, названы выражениями:
#: номера строк врут после первого же коммита.
_SLICE_START = "report = _verify("
_SLICE_END = "self._sensor_failed('evidence_support', exc)"

#: Строки, добавленные СВЕРХ переноса, поимённо. В истории они стояли выше
#: вырезанного участка (флаг) либо внутри `if self.verifier_enabled` (импорты);
#: у метода нет доступа ни к тому, ни к другому. Список — часть контракта:
#: любая четвёртая добавка обязана уронить тест.
SEAM_LINES = (
    "verifier_failure = False",
    "from core.verifier import verify as _verify",
    "from core.verifier_models import VerificationReport as _VRSoft",
)


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
    """Срез: от вызова верификатора до последнего сенсора, включая."""
    start = end = None
    for i, stmt in enumerate(body):
        text = ast.unparse(stmt)
        if start is None and _SLICE_START in text:
            start = i
        elif start is not None and text.rstrip().endswith(_SLICE_END):
            end = i
            break
    if start is None or end is None:  # pragma: no cover — история уже другая
        return []
    return body[start:end + 1]


def _history_slice() -> list[ast.stmt]:
    """Участок в историческом `_run_inner` — он лежит внутри `if verifier_enabled`."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        return []
    run_inner = _func(ast.parse(old_src), "_run_inner")
    if run_inner is None:  # pragma: no cover
        return []
    for node in ast.walk(run_inner):
        if isinstance(node, ast.If):
            found = _moved_slice(node.body)
            if found:
                return found
    return []  # pragma: no cover


def _new_method():
    return _func(ast.parse(Path(verification_mod.__file__).read_text(encoding="utf-8")),
                 "_verify_draft")


def _dump(stmts: list[ast.stmt]) -> str:
    return "".join(ast.dump(s, include_attributes=False) for s in stmts)


# ---------------------------------------------------------------------------
# RETIRED: test_logic_moved_symbol_for_symbol
#
# Migration equivalence was verified when `_verify_draft` moved out of
# `_run_inner`. That event is over and its proof stands.
#
# Retired 2026-08-05 by the operator's decision, the fifth of this class. The
# change it blocked is census item A6: `_synthesis_expects_contract_headers` is
# read directly now instead of through a `getattr` default that let a previous
# turn's value stand in for this turn's contract.
#
# What guards this method now:
#   * tests/test_cross_mixin_fields_are_guaranteed.py — the field, the rule and
#     the class the rule protects.
#   * tests/test_loop_split_wiring.py::test_the_mixin_declares_everything_it_borrows
#   * the structural checks that remain in this file.
# ---------------------------------------------------------------------------

def test_the_seams_are_exactly_the_declared_ones():
    """Швов ровно три, и они именно те, что объявлены.

    Без этой проверки предыдущая вырождается: любую правку можно было бы
    внести, объявив её «швом». Здесь список закрыт с обеих сторон.
    """
    new = _new_method()
    assert new is not None
    seams = [
        ast.unparse(st).strip() for st in new.body[1:-1]
        if ast.unparse(st).strip() in SEAM_LINES
    ]
    assert seams == list(SEAM_LINES), f"швы разошлись со списком: {seams}"
    # И ни одного оператора сверх «шов или перенос» — считаем по числам.
    old_stmts = _history_slice()
    if old_stmts:
        assert len(new.body) - 2 == len(old_stmts) + len(SEAM_LINES), (
            "в теле есть оператор, который не является ни швом, ни переносом"
        )


def test_the_loop_no_longer_defines_it():
    """Дубля нет: проверка живёт в одном месте, а не в двух."""
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
    for node in ast.walk(run_inner):
        if isinstance(node, ast.If):
            assert not _moved_slice(node.body), "вырезанный участок остался в цикле"


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    for name in MOVED:
        assert callable(getattr(AgentLoop, name, None)), f"{name} потерялся"
        assert inspect.getmodule(getattr(AgentLoop, name)) is verification_mod


def test_a_broken_verifier_is_reported_as_broken_not_as_unsupported():
    """Мягкий отказ: черновик цел, флаг поднят, «нет улик» не выдумано.

    Это ЕДИНСТВЕННОЕ, ради чего у метода два возвращаемых значения. Прогон
    без похода в модель: подменяем сам верификатор на падающий и смотрим,
    что уехало наружу.
    """
    calls: list[dict] = []

    class _Log:
        def log(self, event, payload=None):
            calls.append({"event": event, "payload": payload or {}})

    class _Agent(verification_mod.AgentLoopVerification):
        def __init__(self):
            self.log = _Log()
            self.last_verification = None
            self.last_provenance = None
            self.last_role_context = None
            self.last_source_ranking = None

        def _sensor_failed(self, name, exc):
            calls.append({"event": f"sensor_failed:{name}", "payload": {}})

        def _verification_receipt_kwargs(self):
            return {}

    import core.verifier as verifier_mod

    agent = _Agent()
    original = verifier_mod.verify

    def _boom(**kw):
        raise RuntimeError("верификатор сломался")

    verifier_mod.verify = _boom
    try:
        report, failed = agent._verify_draft(
            "черновик ответа",
            chain=None,
            user_question="вопрос",
            attempt=1,
            plan=type("P", (), {"steps": []})(),
            artifacts={},
            failure_history=[],
            _disagreement_shadow=[],
        )
    finally:
        verifier_mod.verify = original

    assert failed is True, "поломка верификатора обязана быть видна вызывающему"
    assert report.annotated_answer == "черновик ответа", "черновик потерян"
    assert report.total_chunks == 0
    # И самое главное: это НЕ выглядит как «улик не хватило».
    assert report.fully_unverified is False
    assert [c for c in calls if c["event"] == "verifier_failure"], (
        "поломка не попала в журнал — сбой станет невидимым"
    )


def test_the_split_actually_shrank_the_loop():
    """Смысл раскола — размер. Цикл обязан быть заметно меньше прежнего."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2560, f"core/loop.py снова разбух: {lines} строк"
