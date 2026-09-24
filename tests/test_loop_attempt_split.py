"""Раскол `core/loop.py`, кусок 10 — цикл попыток и объект состояния прогона.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Куски 1–9 переезжали ДОСЛОВНО, символ в символ. Этот — нет, и тест
устроен иначе.

Цикл попыток держится за 22 run-локали `_run_inner`, а `ruff.toml` этого же
репозитория ставит `max-args = 12`: списком параметров он не переносится.
Поэтому состояние названо явно (`AttemptState`), а тело получено из истории
одной подстановкой `имя -> st.имя` по этим 22 полям.

Сила сверки та же, что у дословной, — просто преобразование объявлено:
берём исторический цикл, применяем ТУ ЖЕ подстановку своим кодом и требуем
совпадения AST. Если в теле поменяли хоть один символ сверх подстановки,
тест падает; если подстановку расширили молча — падает тоже, потому что
список полей здесь свой и сверяется с полями датакласса.
"""
from __future__ import annotations

import ast
import inspect
import subprocess  # nosec B404 — читаем историю через git show, вход фиксирован
from dataclasses import fields as dataclass_fields
from pathlib import Path

import pytest

import core.loop as loop_mod
import core.loop_attempt as attempt_mod
from core.loop import AgentLoop
from core.loop_attempt import AttemptState

_REPO = Path(__file__).resolve().parents[1]

#: Ровно те имена, что стали полями состояния. Список ДУБЛИРУЕТ датакласс
#: намеренно: он — вторая независимая запись преобразования, и расхождение
#: между ними обязано быть видно (см. `test_the_substitution_matches_the_state`).
SUBSTITUTED = frozenset({
    "_cp", "_run_assumptions", "_stagnation_shadow", "_task_planner_llm",
    "advice_for_planner", "artifacts", "attempt", "chain", "cheap_path_active",
    "failure_history", "file_hint", "forbidden_actions", "forced_reasoning",
    "forced_sources", "forced_warnings", "goal", "local_critique_active",
    "plan", "planner_history", "planner_out", "replan_exhausted",
    "user_question",
})

#: Имя носителя состояния внутри перенесённого тела.
HOLDER = "st"


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


def _the_attempt_loop(fn: ast.AST) -> ast.While | None:
    """Единственный `while True` верхнего уровня в теле — цикл попыток."""
    loops = [
        st for st in fn.body
        if isinstance(st, ast.While)
        and isinstance(st.test, ast.Constant) and st.test.value is True
    ]
    return loops[0] if len(loops) == 1 else None


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


class _DeclaredDeletions(ast.NodeTransformer):
    """Санкционированные УДАЛЕНИЯ из исторического тела — по одному, поимённо.

    Тот же принцип, что у `_Substitute`: правка истории допустима только как
    объявленное преобразование, а не как молчаливое расширение допуска.

    2026-08-08, вердикт оператора: кэш планировщика удалён из кода. Замер
    (C08): собственный инвалидатор — mtime эпизодного хранилища в ключе —
    делал кэш недостижимым в любом профиле с банковкой эпизодов (0 попаданий
    на двух прогонах одного вопроса), а наблюдателей у компонента не было
    вовсе (обе ломки M49/M50 остались зелёными). Здесь из ИСТОРИЧЕСКОГО тела
    вычищаются ровно три конструкции кэша, чтобы сверка продолжала держать
    всё остальное тело символ в символ:

      1. `_pc_key = (...)` — присваивание ключа;
      2. `if ... _pc_key in self._planner_cache: <hit> else: <plan>` —
         ветвление на попадание; остаётся его else-ветка (настоящий вызов
         планировщика);
      3. `if ...: self._planner_cache[_pc_key] = ...` — сохранение в кэш.
    """

    @staticmethod
    def _mentions_cache(node: ast.AST) -> bool:
        return "_planner_cache" in ast.unparse(node)

    def visit_Assign(self, node: ast.Assign):
        if any(isinstance(t, ast.Name) and t.id == "_pc_key" for t in node.targets):
            return None
        return self.generic_visit(node)

    def visit_If(self, node: ast.If):
        node = self.generic_visit(node)
        if self._mentions_cache(node.test):
            # ветвление на попадание: остаётся только else-ветка
            return node.orelse
        if node.body and all(self._mentions_cache(s) for s in node.body) and not node.orelse:
            # сохранение в кэш: тело состоит только из кэшевых строк
            return None
        return node


class _DeclaredMoves(ast.NodeTransformer):
    """Санкционированные ПЕРЕСТАНОВКИ — по одной, поимённо.

    2026-08-14. `st.failure_history.extend(attempt_failures)` стоял НИЖЕ
    успешного `break`, поэтому попытка, где один шаг упал, а другой дал
    артефакт, выбрасывала свои триггеры целиком: они оставались только в
    журнале. Замер на живом прогоне — `file_read README.md` вернул
    FileNotFoundError рядом с удачным шагом, и агент на вопрос «существует ли
    файл» мог ответить только «подтвердить нельзя». Пробел был записан
    2026-08-09 в `tests/test_partial_attempt_failure_reporting.py`, и его
    формулировка называет ровно этот `break`.

    Строка переехала ВЫШЕ ветвления. Здесь та же перестановка применяется к
    ИСТОРИЧЕСКОМУ телу, чтобы сверка продолжала держать всё остальное символ
    в символ: объявленная правка, а не расширение допуска.
    """

    _EXTEND = "st.failure_history.extend(attempt_failures)"

    def visit_While(self, node: ast.While):
        node = self.generic_visit(node)
        body = list(node.body)
        for i, stmt in enumerate(body):
            if not (isinstance(stmt, ast.If) and i + 1 < len(body)):
                continue
            nxt = body[i + 1]
            if ast.unparse(nxt).strip() != self._EXTEND:
                continue
            if not any(isinstance(s, ast.Break) for s in stmt.body):
                continue
            body[i], body[i + 1] = nxt, stmt
            node.body = body
            return node
        return node


class _DeclaredInsertions(ast.NodeTransformer):
    """Санкционированные ВСТАВКИ — по одной, поимённо.

    2026-08-22, ревизия починки MIR-026
    (`CLOSURE_AUDIT_2026-08-22.md in git history`). Правило поля для «проставить
    статус на выходе»: статус, записанный на ОДНОМ выходе, врёт про остальные.
    У цикла попыток нет ни одного `return`, но есть один голый `raise` —
    перевыброс `ModelBudgetExceeded` после сохранения контрольной точки. Он
    уходил мимо простановки, и Goal/Plan оставались `pending`, что на этом
    языке значит «не начинался», а не «прерван по бюджету».

    Вставка применяется и к ИСТОРИЧЕСКОМУ телу, чтобы сверка остального
    осталась посимвольной: объявленная правка, а не расширение допуска.
    """

    _SETTLE = "self._settle_run_objects(st, 'failed')"

    def __init__(self) -> None:
        self.inserted = 0

    def _walk_body(self, body: list) -> list:
        out: list = []
        for stmt in body:
            if isinstance(stmt, ast.Raise) and stmt.exc is None:
                out.append(ast.parse(self._SETTLE).body[0])
                self.inserted += 1
            out.append(stmt)
        return out

    def generic_visit(self, node: ast.AST):
        node = super().generic_visit(node)
        for field in ("body", "orelse", "finalbody"):
            body = getattr(node, field, None)
            if isinstance(body, list) and any(isinstance(s, ast.stmt) for s in body):
                setattr(node, field, self._walk_body(body))
        return node


class _DeclaredDropRecording(ast.NodeTransformer):
    """Санкционированная ВСТАВКА №2 — запись сброшенных шагов как провалов.

    2026-09-05, экзамен, ходы 35–36 (`EXAM_SELF_KNOWLEDGE_2026-09-05.md in git history`).
    Санитайзер сбросил шаг поиска за `{` в аргументе и записал причину только
    в `warnings` события `planner`; план исполнился без шага, синтезатор не
    узнал, что шага не было, и на вопрос «почему поиск не выполнился» агент
    назвал чужую ошибку и спланировал тот же аргумент снова.

    Перед циклом исполнения шагов вставляется ОДНА инструкция: каждое
    предупреждение вида `step[N]: … dropped` становится триггером
    `step_dropped` в `attempt_failures` (`core/replan.py::dropped_step_triggers`),
    откуда оно доходит и до синтезатора (`<failure_context>`), и до
    перепланирования. Вставка применяется и к ИСТОРИЧЕСКОМУ телу — объявленная
    правка, не расширение допуска.
    """

    _RECORD = (
        "attempt_failures.extend("
        "dropped_step_triggers(st.planner_out.warnings, attempt=st.attempt))"
    )

    def __init__(self) -> None:
        self.inserted = 0

    @staticmethod
    def _is_step_execution(stmt: ast.stmt) -> bool:
        return (
            isinstance(stmt, ast.For)
            and isinstance(stmt.iter, ast.Call)
            and isinstance(stmt.iter.func, ast.Attribute)
            and stmt.iter.func.attr == "_execute_steps_parallel"
        )

    def visit_While(self, node: ast.While):
        node = self.generic_visit(node)
        body: list = []
        for stmt in node.body:
            if self._is_step_execution(stmt):
                body.append(ast.parse(self._RECORD).body[0])
                self.inserted += 1
            body.append(stmt)
        node.body = body
        return node


class _DeclaredObservationRound(ast.NodeTransformer):
    """Санкционированная ЗАМЕНА №3 — прочитанное не конец хода.

    2026-09-19, экзамен из тридцати задач с проверкой кодом. Ветка успеха
    заменяла результаты последней попыткой и сразу выходила к ответу, поэтому
    «прочитай — посчитай — запиши» внутри хода было невыполнимо. Теперь
    результаты копятся, и `core/observation_round.py::continue_after_observation`
    вправе вернуть цикл к планировщику с увиденным. Замена применяется и к
    ИСТОРИЧЕСКОМУ телу — объявленная правка, не расширение допуска.
    """

    _OLD = "st.artifacts = attempt_artifacts\nst.chain = attempt_chain\nbreak"
    _NEW = (
        "st.artifacts = {**st.artifacts, **attempt_artifacts}\n"
        "st.chain = ProvenanceChain([*st.chain.evidences, *attempt_chain.evidences])\n"
        "if continue_after_observation(self, st, attempt_artifacts):\n"
        "    continue\n"
        "break"
    )

    def __init__(self) -> None:
        self.replaced = 0

    def visit_If(self, node: ast.If):
        node = self.generic_visit(node)
        if "\n".join(ast.unparse(s) for s in node.body) == self._OLD:
            node.body = ast.parse(self._NEW).body
            self.replaced += 1
        return node


#: Правки цикла ПОСЛЕ переноса, объявленные задним числом 2026-09-24. С 19.09
#: история была обрезана, этот тест молча уходил в skip, и две намеренные правки
#: легли без объявления. Когда полная история вернулась с GitHub, тест их сразу
#: назвал. Каждая — (было, стало, коммит); было обязано встретиться ровно
#: один раз в нормализованном тексте (ast.unparse), иначе правку надо объявить
#: заново. Остальное тело по-прежнему сверяется с историей символ в символ.
_DECLARED_EDITS = (
    (  # 483f2e4, 8cb2f50 (2026-09-20): датчик «довод ↔ действие» читает обоснование шага
        "_ra_report = check_reasoning_actions(st.planner_out.reasoning, [s['tool'] for s in st.planner_out.sources])",
        ("_sources = list(st.planner_out.sources)\n"
        "        _with_rationale = sum((1 for s in _sources if s.get('rationale')))\n"
        "        _ra_mode = 'rationale' if _with_rationale else 'keywords'\n"
        "        _ra_report = check_by_rationale(_sources) if _with_rationale else "
        "check_reasoning_actions(st.planner_out.reasoning, [s['tool'] for s in _sources])"),
    ),
    (  # 8cb2f50 (2026-09-20): журнал говорит, каким способом судила проверка
        "self.log.log('reasoning_action_mismatch', {**_ra_report.to_log_payload(), 'attempt': st.attempt})",
        ("self.log.log('reasoning_action_mismatch', {**_ra_report.to_log_payload(), 'mode': _ra_mode, "
        "'steps_with_rationale': _with_rationale, 'steps_total': len(_sources), 'attempt': st.attempt})"),
    ),
    (  # 248122d, 73a5518 (2026-09-21/22): прочитанное не перечитывается, запись после свежего чтения ждёт
        "self._execute_steps_parallel(st.plan.steps)",
        "self._execute_steps_parallel(steps_to_run(self, st, attempt_artifacts))",
    ),
)


def _apply_declared_edits(src: str) -> str:
    for before, after in _DECLARED_EDITS:
        assert src.count(before) == 1, (
            f"объявленная правка встречается {src.count(before)} раз вместо одного — "
            f"объяви её заново: {before[:80]}")
        src = src.replace(before, after, 1)
    return src


def test_the_loop_moved_under_one_declared_substitution():
    """История + объявленная подстановка = то, что лежит в новом модуле."""
    old_src = _history()
    if not old_src.strip():  # pragma: no cover — поверхностный клон без истории
        pytest.skip("история недоступна (shallow clone) — сверку не выполнить")
    old_run_inner = _func(ast.parse(old_src), "_run_inner")
    assert old_run_inner is not None, "в истории нет `_run_inner` — сверять не с чем"
    old_loop = _the_attempt_loop(old_run_inner)
    if old_loop is None:  # pragma: no cover — история уже без цикла
        pytest.skip("цикл попыток в истории не найден — раскол уже зафиксирован")

    new_method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
    )
    assert new_method is not None, "`_run_attempt_loop` пропал из нового модуля"
    new_loop = _the_attempt_loop(new_method)
    assert new_loop is not None, "в новом методе должен быть ровно один `while True`"

    insertions = _DeclaredInsertions()
    drop_recording = _DeclaredDropRecording()
    observation_round = _DeclaredObservationRound()
    expected = ast.fix_missing_locations(
        observation_round.visit(
            drop_recording.visit(
                insertions.visit(
                    _DeclaredMoves().visit(
                        _DeclaredDeletions().visit(
                            _Substitute().visit(ast.parse(ast.unparse(old_loop)))
                        )
                    )
                )
            )
        )
    )
    assert observation_round.replaced == 1, (
        "замена ветки успеха рассчитана РОВНО на одну такую ветку, а их "
        f"{observation_round.replaced} — правку надо объявить заново"
    )
    assert insertions.inserted == 1, (
        "объявленная вставка рассчитана РОВНО на один голый `raise` в теле "
        f"цикла, а их {insertions.inserted} — правку надо объявить заново"
    )
    assert drop_recording.inserted == 1, (
        "запись сброшенных шагов рассчитана РОВНО на один цикл исполнения шагов, "
        f"а их {drop_recording.inserted} — правку надо объявить заново"
    )
    expected = ast.parse(_apply_declared_edits(ast.unparse(expected)))
    got = ast.parse(ast.unparse(new_loop))
    assert ast.dump(expected) == ast.dump(got), (
        "тело цикла отличается от истории СВЕРХ объявленной подстановки и "
        "объявленных удалений — это уже не перенос"
    )


def test_the_substitution_matches_the_state():
    """Список подстановки и поля датакласса — одно и то же, в обе стороны.

    Без этого предыдущий тест вырождается: расширив `SUBSTITUTED`, можно было
    бы «объявить» любую правку частью переноса.
    """
    declared = {f.name for f in dataclass_fields(AttemptState)}
    assert declared == set(SUBSTITUTED), (
        "поля состояния разошлись со списком подстановки: "
        f"только в датаклассе {sorted(declared - set(SUBSTITUTED))}, "
        f"только в списке {sorted(set(SUBSTITUTED) - declared)}"
    )


def test_the_body_touches_no_run_local_outside_the_state():
    """В теле не осталось голых run-локалей: всё либо в `st`, либо своё.

    Если бы какое-то имя забыли внести в состояние, оно бы читалось из
    ниоткуда — и упало бы уже на прогоне. Но обратный случай тише: имя,
    оставшееся ЛОКАЛЬНЫМ внутри метода, работает, а наружу не попадает. Так
    выход цикла может молча перестать доезжать до вызывающего.
    """
    method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
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
        f"{sorted(bound & set(SUBSTITUTED))} — выход цикла не доедет до вызывающего"
    )


def test_the_caller_unpacks_exactly_the_declared_outputs():
    """Что объявлено выходом — вызывающий обязан забрать, и только это.

    Молчаливая потеря выхода — самый тихий способ сломать этот перенос:
    цикл отработает, результат ляжет в `st`, и никто его не заберёт. В обе
    стороны: лишняя распаковка означает, что `OUTPUTS` устарел.
    """
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    unpacked = {
        node.attr for node in ast.walk(run_inner)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == "_attempt_state"
        and isinstance(node.ctx, ast.Load)
    }
    assert unpacked == set(AttemptState.OUTPUTS), (
        f"распаковка разошлась с объявленными выходами: "
        f"не забрали {sorted(set(AttemptState.OUTPUTS) - unpacked)}, "
        f"забрали лишнее {sorted(unpacked - set(AttemptState.OUTPUTS))}"
    )


def test_every_declared_output_is_actually_written_by_the_loop():
    """Выход, которому цикл ничего не присваивает, — не выход.

    Без этого `OUTPUTS` можно было бы наполнить чем угодно: распаковка
    сойдётся, а смысла в ней не будет.
    """
    method = _func(
        ast.parse(Path(attempt_mod.__file__).read_text(encoding="utf-8")),
        "_run_attempt_loop",
    )
    assert method is not None
    written = {
        node.attr for node in ast.walk(method)
        if isinstance(node, ast.Attribute)
        and isinstance(node.value, ast.Name) and node.value.id == HOLDER
        and isinstance(node.ctx, ast.Store)
    }
    idle = sorted(set(AttemptState.OUTPUTS) - written)
    assert not idle, f"объявлены выходом, но цикл их не пишет: {idle}"


def test_the_agent_still_has_the_moved_method():
    """Для потребителя ничего не изменилось: класс собран из миксинов."""
    assert callable(getattr(AgentLoop, "_run_attempt_loop", None))
    assert inspect.getmodule(AgentLoop._run_attempt_loop) is attempt_mod


def test_the_loop_no_longer_defines_it():
    """Дубля нет: цикл попыток живёт в одном месте, а не в двух."""
    run_inner = _func(ast.parse(Path(loop_mod.__file__).read_text(encoding="utf-8")),
                      "_run_inner")
    assert run_inner is not None
    assert _the_attempt_loop(run_inner) is None, "цикл попыток остался в `_run_inner`"


def test_the_operator_ceiling_is_finally_met():
    """Ради чего всё затевалось: файл уложился в операторские 2000 строк."""
    lines = len(Path(loop_mod.__file__).read_text(encoding="utf-8").splitlines())
    assert lines < 2000, f"core/loop.py снова выше потолка оператора: {lines} строк"
