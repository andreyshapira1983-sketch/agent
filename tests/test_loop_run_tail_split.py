"""Раскол `core/loop.py`, кусок 7 — хвост прогона живёт отдельно.

Правила оператора: «разбирай большие файлы на компактные подключаемые
модули — не дублируя, не искажая» и «ни один файл кода не длиннее 2000
строк». Сюда уехало всё между «текст ответа окончателен» и «эпизод
записан»: последняя редакция, теневые сенсоры, запись хода, уплотнение
памяти, профиль и допущения.

**Дословность больше не проверяется, и это осознанно** — причина записана
на месте снятого сторожа ниже. Осталось то, за что в этом участке платят
дороже, чем строками.

Первый порядок: запись эпизода обязана ОСТАТЬСЯ в цикле и идти после —
успех, записанный раньше, оставляет окно, где последующий сбой роняет ход
с уже забаненным успехом. Второй: в эпизод обязан уехать тот же текст, что
и пользователю, то есть уже отредактированный, иначе память сохранит
секрет, который из ответа вырезали. Плюс отсутствие дубля, доступность
метода потребителю и собственно размер цикла.
"""
from __future__ import annotations

import ast
import inspect
from pathlib import Path

import core.loop as loop_mod
import core.loop_run_tail as tail_mod
from core.loop import AgentLoop

_REPO = Path(__file__).resolve().parents[1]

MOVED = ("_finalize_run_tail",)

_SLICE_START = "safe_answer, answer_findings, answer_pii_findings = redact_dlp_text(answer)"
_SLICE_END_MARKER = "self.assumption_store.save_many("


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


# --- `test_logic_moved_symbol_for_symbol` снят 2026-08-05 --------------------
#
# Сторож сверял тело `_finalize_run_tail` с срезом `_run_inner` из коммита
# 76941e0 и требовал СОВПАДЕНИЯ ДО УЗЛА AST. Он был прав ровно на время
# переезда: доказывал, что перенос — это перенос. Переезд давно в истории, и с
# тех пор сторож означал другое — «хвост прогона больше нельзя менять».
#
# Наткнулись на это, закрывая A7: два обработчика здесь глотали сбой молча
# (профиль и хранилище допущений), и починка их упиралась в сторож. То есть
# он не поймал дефект — он защищал его от исправления.
#
# Что держит участок вместо него. Пять проверок в этом файле остались, и они
# про существо, а не про буквы: дубля нет, метод доступен потребителю, эпизод
# банкуется ПОСЛЕ хвоста, в эпизод уезжает отредактированный текст, цикл
# действительно уменьшился.
#
# `_history` и `_BEFORE_THE_SPLIT` ушли вместе с ним: других читателей у них
# не было. `_moved_slice` остался — им пользуется проверка «дубля нет».


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
