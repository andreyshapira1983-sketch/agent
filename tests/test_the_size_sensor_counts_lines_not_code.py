"""MIR-099: the size sensor measures CODE, and says so in its quote.

HISTORY. This file used to pin the OLD behaviour — total line count — on the
operator's explicit ruling: the sensor stays untouched «пока не установит,
насколько total LOC действительно коррелирует с тем, что вы хотите считать
переросшим модулем». That precondition was satisfied on 2026-08-22 by the
275-module census: Pearson r(total, code) = 0.971 globally — AND 5 of the 10
live verdicts still flip at the threshold, because a correlation describes the
cloud while a sensor makes a cut, and prose share among large modules spans
1%–38%. A high correlation is the wrong statistic for a threshold instrument.

THE DECIDING MEASUREMENT: the errors were one-directional. Zero modules sat
under 800 total with 800+ of code, so counting code REMOVES the five noise
verdicts and cannot newly miss anything. The sensor now counts code lines
(docstrings and pure-comment lines excluded, a line carrying code plus a
trailing comment is code — the census rule), and its quote names BOTH
quantities, which is MIR-099's closure criterion verbatim.

The specimen this entry was opened on: `core/self_task_producer.py`, 870 total
but 602 of code — the agent's first self-chosen engineering proposal, produced
by a sensor that was measuring explanations written in the margins.

ЧТО ИЗМЕНИЛОСЬ 2026-09-23. Счётчик остался и меряет ровно то же, но КАНДИДАТА
НА РАЗДЕЛЕНИЕ он больше не назначает: длина модуля — хоть кодом, хоть всего —
перестала быть поводом дробить (договор оператора о дроблении, замер живого
предложения по `core/loop_step_execution.py`; см.
tests/test_backlog_oversized_module.py). Поэтому измерения ниже проверяются
у самого счётчика, а не через сигнал: свойство осталось, потребитель сменился.
"""
from __future__ import annotations

from core.backlog_signals import _code_line_count


def _flagged_by_size(rel: str, content: str) -> bool:
    """Сработал бы прежний сигнал: код (или всего, если модуль немой) >= 800."""
    code, parsed = _code_line_count(content)
    del rel
    return (code if parsed else content.count(chr(10)) + 1) >= 800


def _module(code_lines: int, prose_lines: int) -> str:
    body = ["# explanation" for _ in range(prose_lines)]
    body += [f"x{i} = {i}" for i in range(code_lines)]
    return "\n".join(body)


def test_a_module_that_is_mostly_prose_is_not_flagged() -> None:
    """The five live noise verdicts, synthesised: big in total, small in code."""
    assert not _flagged_by_size(
        "core/mostly_prose.py", _module(code_lines=100, prose_lines=800)), (
        "a module large only in its margins was proposed for splitting — the "
        "sensor is measuring explanations again"
    )


def test_a_module_of_real_code_is_flagged() -> None:
    assert _flagged_by_size("core/big_code.py", _module(code_lines=900, prose_lines=0))


def test_the_counter_separates_code_from_prose() -> None:
    """Обе величины по-прежнему различимы — на этом и стоял MIR-099."""
    content = _module(code_lines=900, prose_lines=100)
    code, parsed = _code_line_count(content)
    assert parsed and code == 900, code
    assert content.count(chr(10)) + 1 == 1000


def test_docstrings_are_prose_too() -> None:
    """Comments were the easy half; a huge module docstring is the shape the
    census actually found in the flagged files."""
    doc = '"""' + "\n" + "\n".join("explanatory prose" for _ in range(800)) + "\n" + '"""'
    code = "\n".join(f"x{i} = {i}" for i in range(100))
    assert not _flagged_by_size("core/doc_heavy.py", doc + "\n" + code)


def test_an_unparseable_module_falls_back_to_total_lines() -> None:
    """The sensor must not go blind on a syntax error: the old proxy is the
    fallback. (Кандидата на разделение это больше не назначает — немой разбор
    теперь молчит в обе стороны, см. tests/test_backlog_oversized_module.py.)"""
    broken = "def broken(:\n" + "\n".join(f"x{i} = {i}" for i in range(900))

    code, parsed = _code_line_count(broken)

    assert not parsed and code == 0
    assert _flagged_by_size("core/broken.py", broken), "счётчик ослеп на разборе"


def test_a_small_module_is_not_flagged() -> None:
    assert not _flagged_by_size("core/small.py", _module(code_lines=50, prose_lines=50))


# ── Audit of this closure (docs/audit/archive/CLOSURE_AUDIT_2026-08-22.md) ──────────
#
# The field's named failure for AST line counting is that it misreads real
# Python shapes. Seven were probed — one-liner ifs, multi-line call arguments,
# big dict literals, chained methods, decorators, trailing comments — and six
# held. The seventh did not: a multi-line string that is NOT a docstring
# counted as ONE line, so `core/planner_prompt.py` read as 8 code lines of
# 553. That is a false-NEGATIVE channel, and the closure's own argument
# ("errors are one-directional, so counting code cannot newly miss anything")
# was a property of the tree at that moment, not of the counter.


def test_a_module_of_embedded_payload_is_not_invisible() -> None:
    """An embedded prompt/SQL/template is bulk the module carries — payload,
    not explanation. Only docstrings and comments are prose."""
    payload = "SQL = '''\n" + "\n".join(f"SELECT {i}" for i in range(900)) + "\n'''"
    assert _flagged_by_size("core/payload.py", payload), (
        "a module that is 900 lines of embedded literal reads as one line — "
        "the old total-lines sensor would have flagged it and this one does not"
    )


def test_a_docstring_is_still_prose_however_long() -> None:
    """The boundary the repair must not cross: explanation stays free."""
    doc = '"""' + "\n" + "\n".join("explanatory prose" for _ in range(900)) + "\n" + '"""'
    code = "\n".join(f"x{i} = {i}" for i in range(50))
    assert not _flagged_by_size("core/doc.py", doc + "\n" + code)


def test_ordinary_python_shapes_are_not_inflated() -> None:
    """Six shapes probed in the audit that held, kept as a regression net."""
    for label, src in (
        ("one-liner ifs", "\n".join(f"if x{i}: y{i} = {i}" for i in range(700))),
        ("dict literal", "D = {\n" + ",\n".join(f'  "k{i}": {i}' for i in range(700)) + "\n}"),
        ("chained calls", "x = (o\n" + "\n".join(f"  .s{i}()" for i in range(700)) + ")"),
        ("trailing comments", "\n".join(f"x{i} = {i}  # why" for i in range(700))),
    ):
        assert not _flagged_by_size(f"core/{label}.py", src), (
            f"{label} was flagged below the threshold")
