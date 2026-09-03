"""Раскол `core/loop.py`, кусок 12 — сборка агента уехала из оркестратора.

Правило оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая». Этот кусок вынут не ради размера, а ради
ответа на вопрос «чем является этот файл»: `core/loop.py` — оркестратор
цикла §3, а конструктор описывает, ИЗ ЧЕГО агент состоит, а не как он ведёт
ход. Вместе с ним уехали 30 импортов, нужных только ему.

Перенос дословный, поэтому сверка как в кусках 1 и 3 — тело и подпись
совпадают с историей символ в символ. Отдельно пинится то, что для
вызывающего ничего не изменилось: `AgentLoop(...)` обязан собираться так же,
`__init__` обязан находиться по MRO, а число и порядок параметров — не
поехать.
"""
from __future__ import annotations

import ast
import copy
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_init as init_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]


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


def _init_of(tree: ast.AST, class_name: str) -> ast.FunctionDef | None:
    for node in ast.walk(tree):
        if isinstance(node, ast.ClassDef) and node.name == class_name:
            for member in node.body:
                if isinstance(member, ast.FunctionDef) and member.name == "__init__":
                    return member
    return None


def _new_init() -> ast.FunctionDef | None:
    return _init_of(
        ast.parse(Path(init_mod.__file__).read_text(encoding="utf-8")), "AgentLoopInit"
    )


#: Санкционированные УДАЛЕНИЯ из исторического конструктора — поимённо, как
#: объявленная подстановка в `test_loop_attempt_split`. 2026-08-08, вердикт
#: оператора: кэш планировщика удалён (замер C08 — недостижим в профилях с
#: банковкой эпизодов, наблюдателей ноль), с ним ушло поле `_planner_cache`.
_DECLARED_DELETED_FIELDS = frozenset({"_planner_cache"})

#: Санкционированные ДОБАВЛЕНИЯ — тем же порядком, что и удаления выше.
#: 2026-08-09, вторая итерация починки уточнений: вопрос, о котором задано
#: уточнение, обязан пережить процесс, а `AgentLoop` не знает своего рабочего
#: каталога — путь, как и все хранилища, приходит извне. Добавка допустима
#: ТОЛЬКО в хвосте подписи: конструктор зовут позиционно, и вставка в середину
#: сдвинула бы всё после неё. Это проверяется отдельно, ниже.
#: 2026-08-15: `causal_store` — хранилище нижней ступени причинной
#: лестницы; приходит извне, как все прочие хранилища. Тоже В ХВОСТЕ.
_DECLARED_ADDED_PARAMS = ("pending_clarification_path", "causal_store")

#: Санкционированные добавления ПОЛЕЙ (не параметров). 2026-08-13: вердикт
#: применимости улик (`last_evidence_support`) хранится рядом с
#: `last_verification`, чтобы сводка проверки читала то же решение, что
#: журналировал наблюдатель, — иначе хвост объявлял «уверенность: нулевая»
#: ходу, которому улики не полагались (трасса da0f132b).
#: 2026-08-15: `last_confidence_vector` — ось соответствия вопросу едет из
#: него в эпизод, и допуск в опыт её спрашивает. Гарантируется здесь, а не
#: значением по умолчанию у читателя: упавший проверяющий до своей
#: установки не доходит, а значение по умолчанию прятало бы поле, которое
#: никто не гарантирует (tests/test_cross_mixin_fields_are_guaranteed).
_DECLARED_ADDED_FIELDS = frozenset({
    "last_evidence_support",
    "last_confidence_vector",
    # 2026-09-03 (Д1, слово оператора): `last_answer_was_clarification` — ворота
    # помечают ход, ответивший вопросом; безлюдный рантайм читает флаг и не
    # пишет вопрос как сделанную работу. Классифицировано per_run в cns_model.
    "last_answer_was_clarification",
    "causal_store",
})


def _mentions_deleted(stmt: ast.stmt) -> bool:
    return any(name in ast.unparse(stmt) for name in _DECLARED_DELETED_FIELDS)


def _mentions_added(stmt: ast.stmt) -> bool:
    return any(
        name in ast.unparse(stmt)
        for name in (*_DECLARED_ADDED_PARAMS, *_DECLARED_ADDED_FIELDS)
    )


def test_logic_moved_symbol_for_symbol():
    """Дословность ЛОГИКИ: тело конструктора совпадает с историей.

    Сверх объявленных удалений — присваивания полей из
    `_DECLARED_DELETED_FIELDS` вычищаются из ИСТОРИЧЕСКОГО тела до сверки,
    чтобы дословность продолжала держать всё остальное.
    """
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old = _init_of(ast.parse(old_src), "AgentLoop")
    if old is None:  # pragma: no cover — история уже без конструктора
        pytest.skip("конструктор в истории не найден — раскол уже зафиксирован")
    new = _new_init()
    assert new is not None, "`__init__` пропал из нового модуля"
    old_body = "".join(
        ast.dump(s, include_attributes=False)
        for s in old.body if not _mentions_deleted(s)
    )
    new_body = "".join(
        ast.dump(s, include_attributes=False)
        for s in new.body if not _mentions_added(s)
    )
    assert old_body == new_body, (
        "тело конструктора изменилось сверх объявленных удалений и добавлений"
    )


def test_the_signature_is_untouched():
    """Подпись не тронута: те же параметры, порядок и значения по умолчанию.

    Конструктор зовут из `app/bootstrap.py` и из десятков тестов позиционно;
    сдвиг хотя бы одного параметра сломал бы их молча — часть аргументов
    одного типа, и подмена прошла бы без ошибки типов.
    """
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old = _init_of(ast.parse(old_src), "AgentLoop")
    if old is None:  # pragma: no cover
        pytest.skip("конструктор в истории не найден")
    new = _new_init()
    assert new is not None
    trimmed = copy.deepcopy(new.args)
    added = [a for a in trimmed.args if a.arg in _DECLARED_ADDED_PARAMS]
    trimmed.args = [a for a in trimmed.args if a.arg not in _DECLARED_ADDED_PARAMS]
    trimmed.defaults = trimmed.defaults[: len(trimmed.defaults) - len(added)]
    assert ast.dump(old.args) == ast.dump(trimmed), (
        "подпись конструктора изменилась сверх объявленных добавлений"
    )


def test_declared_additions_are_appended_and_optional():
    """Добавка живёт В ХВОСТЕ и со значением по умолчанию.

    Это и есть свойство, которое охраняет сверка подписи: параметры передают
    позиционно, часть из них одного типа, и вставка в середину подменила бы
    аргументы молча. Без этой проверки объявление добавления сняло бы стража
    целиком, а не расширило его на один названный параметр.
    """
    new = _new_init()
    assert new is not None
    names = [a.arg for a in new.args.posonlyargs + new.args.args]
    tail = names[len(names) - len(_DECLARED_ADDED_PARAMS):]
    assert tail == list(_DECLARED_ADDED_PARAMS), (
        f"объявленные добавления обязаны стоять в хвосте подписи, а стоят: {names}"
    )
    assert len(new.args.defaults) >= len(_DECLARED_ADDED_PARAMS), (
        "добавленный параметр обязан иметь значение по умолчанию — иначе он "
        "ломает каждый прежний вызов конструктора"
    )


def test_the_loop_no_longer_defines_it():
    """Дубля нет: конструктор живёт в одном месте, а не в двух."""
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    assert _init_of(tree, "AgentLoop") is None, "конструктор остался в `core/loop.py`"


def test_the_constructor_still_resolves_for_the_caller():
    """Для вызывающего ничего не изменилось: `__init__` находится по MRO."""
    assert inspect.getmodule(AgentLoop.__init__) is init_mod
    # И ровно один конструктор среди примесей — иначе порядок баз начал бы
    # решать, каким собирается агент, а это уже не перенос.
    owners = [
        base for base in AgentLoop.__mro__
        if "__init__" in vars(base) and base is not object
    ]
    assert owners == [init_mod.AgentLoopInit], (
        f"конструктор объявлен не в одном месте: {[b.__name__ for b in owners]}"
    )


def test_the_orchestrator_kept_only_orchestration_members():
    """В классе цикла остались методы хода, а не сборки.

    Проверяемое свойство куска: `core/loop.py` — про то, КАК ведётся ход.
    Появление здесь второго `__init__` или фабрики означает, что сборка
    поползла обратно.
    """
    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    cls = next(
        n for n in ast.walk(tree)
        if isinstance(n, ast.ClassDef) and n.name == "AgentLoop"
    )
    names = {
        m.name for m in cls.body
        if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
    }
    forbidden = {"__init__", "__new__", "create", "build", "from_config"}
    assert not (names & forbidden), f"сборка вернулась в оркестратор: {sorted(names & forbidden)}"


def test_the_imports_left_with_it():
    """Смысл переноса — не только строки метода, но и его шапка.

    30 импортов были нужны только конструктору и делали шапку цикла
    непрочитываемой. Если они вернутся, файл снова начнёт расти с того конца,
    который никто не читает.
    """
    loop_src = Path(loop_mod.__file__).read_text(encoding="utf-8")
    for name in ("LLMPlanner", "PolicyGate", "TraceLogger", "WorkingMemory",
                 "UserProfileStore", "PersistentMemoryStore", "ApprovalProvider"):
        assert f"import {name}" not in loop_src and f"    {name},\n" not in loop_src, (
            f"импорт {name} вернулся в оркестратор — он нужен только сборке"
        )
