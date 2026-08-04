"""Раскол `core/loop.py`: собран ли он обратно.

Шестнадцать кусков разошлись по пятнадцати модулям. Каждый объявляет, что
берёт у хоста, через блок ``if TYPE_CHECKING`` — но объявление это до сих пор
не проверял НИКТО: во время выполнения оно ничего не создаёт, а анализаторы
читают его как подсказку типов, а не как обязательство.

Цена ошибки тут тихая. Примесь, объявившая поле, которого хост не создаёт,
работает ровно до той ветки, где поле впервые читают, — а ветки эти по
большей части редкие (отказ, исчерпанный бюджет, сломавшийся сенсор). Именно
так в этом расколе уже дважды проходили дефекты: осиротевший декоратор и
пропавший импорт — оба пережили и линтер, и весь набор, пока не наткнулись
на горячий путь или на журнал сенсоров.

Поэтому здесь собирается НАСТОЯЩИЙ ``AgentLoop`` и сверяется с тем, что
примеси о нём заявили. Плюс четыре структурные проверки сцепки: ни один
метод не потерян при переезде, ни один не определён дважды, ни одна примесь
не осталась неподключённой и ни один модуль раскола не оказался мёртвым.
"""
from __future__ import annotations

import ast
import importlib
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from pathlib import Path

import pytest

import core.loop as loop_mod
from core.llm import LLM
from core.logger import TraceLogger
from core.loop import AgentLoop, new_trace_id
from core.models import Plan
from core.planner import PlannerOutput
from core.policy import PolicyGate
from tools.base import ToolRegistry

_REPO = Path(__file__).resolve().parents[1]
_CORE = _REPO / "core"

#: Модули раскола. `loop_helpers` не примесь (свободные функции), `loop` — хост.
_NOT_MIXINS = frozenset({"loop", "loop_helpers"})


class _SilentLLM(LLM):
    """Хватает, чтобы собрать цикл: до модели ни один тест здесь не доходит."""

    def __init__(self) -> None:
        pass

    def complete(self, *_a, **_kw) -> str:
        return ""


class _EmptyPlanner:
    def plan(self, *_a, **_kw) -> PlannerOutput:
        return PlannerOutput(plan=Plan(steps=[]), reasoning="", sources=[])


@pytest.fixture()
def agent(tmp_path: Path) -> AgentLoop:
    registry = ToolRegistry()
    return AgentLoop(
        registry=registry,
        policy=PolicyGate(registry),
        llm=_SilentLLM(),
        logger=TraceLogger(
            trace_id=new_trace_id(), log_dir=tmp_path / "logs", verbose=False
        ),
        planner=_EmptyPlanner(),
    )


def _mixin_modules() -> list[str]:
    return sorted(
        p.stem for p in _CORE.glob("loop*.py") if p.stem not in _NOT_MIXINS
    )


def _declared_host_contract(module_name: str) -> set[str]:
    """Имена, объявленные примесью в блоке ``if TYPE_CHECKING``."""
    mod = importlib.import_module(f"core.{module_name}")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    declared: set[str] = set()
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for node in ast.walk(cls):
            if not (isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    declared.add(stmt.target.id)
    return declared


@pytest.mark.parametrize("module_name", _mixin_modules())
def test_the_host_supplies_everything_the_mixin_declares(module_name: str, agent: AgentLoop):
    """Что примесь заявила — цикл обязан иметь ПОСЛЕ прогона.

    Сверяем на отработавшем объекте, а не на свежесобранном: часть полей
    заводится прогонными (`_defect_signals`, `_synthesis_expects_contract_headers`
    и соседи) — они появляются в `_run_inner` и умирают вместе с ходом. Требовать
    их от конструктора значило бы требовать, чтобы состояние прогона пережило
    прогон, а именно от этого здесь и уходили.

    Это единственное место, где `if TYPE_CHECKING` перестаёт быть подсказкой
    анализатору и становится проверяемым обязательством.
    """
    declared = _declared_host_contract(module_name)
    if not declared:
        pytest.skip(f"core/{module_name} не объявляет контракта хоста")
    agent.run("привет")  # заводим прогонные поля
    missing = sorted(name for name in declared if not hasattr(agent, name))
    assert not missing, (
        f"core/{module_name} рассчитывает на {missing}, но цикл их не создаёт — "
        "примесь сломается на первой же ветке, которая их читает"
    )


@pytest.mark.parametrize("module_name", _mixin_modules())
def test_the_mixin_uses_everything_it_declares(module_name: str):
    """Объявление, которым примесь не пользуется, — ложь о её потребностях.

    Обратная сторона предыдущей проверки, и ловит она мою же ошибку: блоки
    `TYPE_CHECKING` при расколе писались руками, и в двух модулях осели поля,
    которых там никто не читает. Само по себе это безвредно — но следующий
    читатель поверит, что примесь их использует, а сопровождающий побоится
    их убрать.
    """
    declared = _declared_host_contract(module_name)
    if not declared:
        pytest.skip(f"core/{module_name} не объявляет контракта хоста")
    mod = importlib.import_module(f"core.{module_name}")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    used = {
        node.attr for node in ast.walk(tree)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "self"
    }
    # Имя может читаться и строкой — через `getattr(self, "имя", …)`.
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name) and node.func.id == "getattr"
            and len(node.args) >= 2
            and isinstance(node.args[1], ast.Constant)
            and isinstance(node.args[1].value, str)
        ):
            used.add(node.args[1].value)
    unused = sorted(declared - used)
    assert not unused, (
        f"core/{module_name} объявляет {unused}, но нигде их не трогает — "
        "объявление врёт о том, что примесь берёт у хоста"
    )


@pytest.mark.parametrize("module_name", _mixin_modules())
def test_every_mixin_is_actually_mixed_in(module_name: str):
    """Модуль раскола, не попавший в базы, — мёртвый код с видом рабочего."""
    mod = importlib.import_module(f"core.{module_name}")
    classes = [
        obj for obj in vars(mod).values()
        if isinstance(obj, type)
        and obj.__module__ == mod.__name__
        and obj.__name__.startswith("AgentLoop")
    ]
    assert classes, f"core/{module_name} не объявляет примеси — модуль осиротел"
    for cls in classes:
        assert cls in AgentLoop.__mro__, (
            f"{cls.__name__} не подключён к AgentLoop — код в нём никогда не исполняется"
        )


def test_no_method_is_defined_twice_in_the_mro():
    """Одно имя — одно определение. Иначе поведение выбирает порядок баз."""
    owners: dict[str, list[str]] = {}
    for base in AgentLoop.__mro__:
        if base is object:
            continue
        for name, value in vars(base).items():
            if callable(value) or isinstance(value, (staticmethod, classmethod)):
                owners.setdefault(name, []).append(base.__name__)
    doubled = {n: o for n, o in owners.items() if len(o) > 1}
    assert not doubled, f"метод определён более одного раза: {doubled}"


def test_nothing_was_lost_on_the_way_out():
    """Каждый метод, что был у `AgentLoop` до раскола, доступен и сейчас."""
    old = subprocess.run(  # nosec B603 B607
        ["git", "show", "HEAD:core/loop.py"],  # noqa: S607
        capture_output=True, cwd=_REPO, check=False,
    ).stdout.decode("utf-8")
    if not old.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    before: set[str] = set()
    for node in ast.walk(ast.parse(old)):
        if isinstance(node, ast.ClassDef) and node.name == "AgentLoop":
            before = {
                m.name for m in node.body
                if isinstance(m, (ast.FunctionDef, ast.AsyncFunctionDef))
            }
    assert before, "в истории нет `AgentLoop` — сверять не с чем"
    lost = sorted(name for name in before if not hasattr(AgentLoop, name))
    assert not lost, f"методы потерялись при расколе: {lost}"


def test_the_agent_still_constructs_and_answers_without_a_model(agent: AgentLoop):
    """Живая сборка: цикл собирается и доходит до ответа на пустом плане.

    Структурные проверки выше не увидят, например, примеси, чей `__init__`
    поссорился с MRO. Здесь цикл действительно запускается.
    """
    answer = agent.run("привет")
    assert isinstance(answer, str)


# ── связи МЕЖДУ примесями ────────────────────────────────────────────────────
# Проверки выше смотрят в одну сторону: что объявлено — то есть. Ниже обратная
# и более важная: что используется — то объявлено. Примесь, зовущая метод
# соседки через MRO и нигде этого не записавшая, работает — но связь между
# модулями существует только в момент прогона, и удаление метода на той стороне
# обнаружится не проверкой, а отказом.


def _members(module_name: str) -> tuple[set[str], set[str], set[str], set[str]]:
    """`(определяет, читает, пишет, объявляет)` для примеси."""
    mod = importlib.import_module(f"core.{module_name}")
    tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
    defines: set[str] = set()
    declares: set[str] = set()
    for cls in (n for n in ast.walk(tree) if isinstance(n, ast.ClassDef)):
        for member in cls.body:
            if isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)):
                defines.add(member.name)
        for node in ast.walk(cls):
            if not (isinstance(node, ast.If) and "TYPE_CHECKING" in ast.unparse(node.test)):
                continue
            for stmt in node.body:
                if isinstance(stmt, ast.AnnAssign) and isinstance(stmt.target, ast.Name):
                    declares.add(stmt.target.id)
                elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    declares.add(stmt.name)
    reads: set[str] = set()
    writes: set[str] = set()
    for node in ast.walk(tree):
        if (
            isinstance(node, ast.Attribute)
            and isinstance(node.value, ast.Name) and node.value.id == "self"
        ):
            (writes if isinstance(node.ctx, ast.Store) else reads).add(node.attr)
    return defines, reads, writes, declares


@pytest.mark.parametrize("module_name", _mixin_modules())
def test_the_mixin_declares_everything_it_borrows(module_name: str):
    """Что примесь берёт у соседок — она обязана записать.

    Это та сторона контракта, которой не было: `self.foo` разрешается по MRO
    и работает, даже если ни одна строка об этой связи не сказана. Цена —
    невидимый граф зависимостей: переименуй метод на той стороне, и узнаешь
    об этом на редкой ветке, а не здесь.

    Замерено при разборе `loop_methods`/`loop_methods2`: таких молчаливых
    связей набралось 21 на девять модулей.
    """
    defines, reads, writes, declares = _members(module_name)
    borrowed = sorted(reads - defines - writes - declares)
    assert not borrowed, (
        f"core/{module_name} берёт {borrowed} у соседних примесей, нигде это не объявив"
    )


@pytest.mark.parametrize("module_name", _mixin_modules())
def test_everything_a_mixin_borrows_actually_exists(module_name: str):
    """Объявленное заимствование обязано кем-то предоставляться.

    Обратная сторона: объявить можно что угодно, в том числе метод, который
    давно переименовали. Тогда контракт выглядит полным, а зовёт в пустоту.
    """
    _defines, _reads, _writes, declares = _members(module_name)
    provided = set(dir(AgentLoop))
    for other in _mixin_modules():
        provided |= _members(other)[0] | _members(other)[2]
    # Прогонные поля (`_defect_signals`, `_executed_tools`, …) заводит сам
    # `_run_inner` в хосте — он тоже поставщик, и его записи входят в набор.
    host = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    provided |= {
        node.attr for node in ast.walk(host)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "self"
        and isinstance(node.ctx, ast.Store)
    }
    orphan = sorted(name for name in declares if name not in provided)
    assert not orphan, (
        f"core/{module_name} объявляет {orphan} — их не даёт ни одна примесь и ни `__init__`"
    )


def test_no_mixin_imports_the_host():
    """Ни одна примесь не импортирует `core.loop`.

    Хост собирает примеси; примесь, потянувшая хост обратно, замыкает круг.
    Сейчас его нет, и цена его появления — не ошибка импорта (Python может
    и вывернуться), а зависимость порядка загрузки от того, кого первым
    тронули. Такое ловится не тестом, а полем.
    """
    offenders = []
    for module_name in _mixin_modules():
        mod = importlib.import_module(f"core.{module_name}")
        tree = ast.parse(Path(mod.__file__).read_text(encoding="utf-8"))
        for node in ast.walk(tree):
            if isinstance(node, ast.ImportFrom) and node.module == "core.loop":
                offenders.append(f"{module_name}:{node.lineno}")
            elif isinstance(node, ast.Import):
                for alias in node.names:
                    if alias.name == "core.loop":
                        offenders.append(f"{module_name}:{node.lineno}")
    assert not offenders, f"примесь импортирует хост: {offenders}"


def test_the_run_state_objects_are_built_with_exactly_their_fields():
    """Сборка состояния прогона в цикле совпадает с датаклассом.

    Три объекта (`AttemptState`, `VerifyState`, `SynthesisState`) — это швы,
    по которым уехали три самых больших куска.

    Проверяемое здесь — не «переданы все поля» (это неправда: чистые выходы
    вроде `answer` вызывающий и не должен заполнять), а более узкое и честное:
    **опустить поле можно, только если оно объявлено выходом.** Обязательные
    поля — тем более обязаны быть переданы, и несуществующих быть не должно.

    Первая версия этой проверки утверждала в docstring, что ловит забытое поле
    с умолчанием, а ловила только обязательные — то есть обещала больше, чем
    делает. Правило ниже действительно закрывает эту дыру: добавь в датакласс
    поле-вход с умолчанием и не передай его — тест упадёт, потому что оно не
    в `OUTPUTS`.
    """
    from dataclasses import MISSING as _MISSING
    from dataclasses import fields as dataclass_fields

    from core.loop_attempt import AttemptState
    from core.loop_synthesis import SynthesisState
    from core.loop_verify_replan import VerifyState

    tree = ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8"))
    calls = {
        node.func.id: {kw.arg for kw in node.keywords}
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        and node.func.id in {"AttemptState", "VerifyState", "SynthesisState"}
    }
    for state in (AttemptState, VerifyState, SynthesisState):
        name = state.__name__
        assert name in calls, f"{name} нигде не собирается — состояние осталось без хозяина"
        passed = calls[name]
        required = {
            f.name for f in dataclass_fields(state)
            if f.default is _MISSING and f.default_factory is _MISSING
        }
        assert required <= passed, (
            f"{name}: обязательные поля не переданы: {sorted(required - passed)}"
        )
        known = {f.name for f in dataclass_fields(state)}
        assert passed <= known, (
            f"{name}: переданы несуществующие поля: {sorted(passed - known)}"
        )
        omitted = known - passed
        assert omitted <= set(state.OUTPUTS), (
            f"{name}: опущены поля, которые не являются выходами: "
            f"{sorted(omitted - set(state.OUTPUTS))} — участок получит умолчание "
            "вместо значения, накопленного циклом, и будет работать почти правильно"
        )
