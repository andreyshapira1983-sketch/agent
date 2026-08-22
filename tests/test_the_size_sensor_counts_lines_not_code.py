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
"""
from __future__ import annotations

from core.backlog_signals import oversized_module_candidates


def _module(code_lines: int, prose_lines: int) -> str:
    body = ["# explanation" for _ in range(prose_lines)]
    body += [f"x{i} = {i}" for i in range(code_lines)]
    return "\n".join(body)


def test_a_module_that_is_mostly_prose_is_not_flagged() -> None:
    """The five live noise verdicts, synthesised: big in total, small in code."""
    records, _ = oversized_module_candidates(
        [("core/mostly_prose.py", _module(code_lines=100, prose_lines=800))]
    )
    assert records == [], (
        "a module large only in its margins was proposed for splitting — the "
        "sensor is measuring explanations again"
    )


def test_a_module_of_real_code_is_flagged() -> None:
    records, _ = oversized_module_candidates(
        [("core/big_code.py", _module(code_lines=900, prose_lines=0))]
    )
    assert [r.target_path for r in records] == ["split:core/big_code.py"]


def test_the_quote_names_both_quantities() -> None:
    """The closure criterion: the sensor states which quantity it measures."""
    records, _ = oversized_module_candidates(
        [("core/big_code.py", _module(code_lines=900, prose_lines=100))]
    )
    quote = records[0].problem_quote
    assert "900 code lines" in quote, quote
    assert "1000 total" in quote, quote


def test_docstrings_are_prose_too() -> None:
    """Comments were the easy half; a huge module docstring is the shape the
    census actually found in the flagged files."""
    doc = '"""' + "\n" + "\n".join("explanatory prose" for _ in range(800)) + "\n" + '"""'
    code = "\n".join(f"x{i} = {i}" for i in range(100))
    records, _ = oversized_module_candidates(
        [("core/doc_heavy.py", doc + "\n" + code)]
    )
    assert records == []


def test_an_unparseable_module_falls_back_to_total_lines() -> None:
    """The sensor must not go blind on a syntax error: the old proxy is the
    fallback, stated as such in the quote."""
    broken = "def broken(:\n" + "\n".join(f"x{i} = {i}" for i in range(900))
    records, _ = oversized_module_candidates([("core/broken.py", broken)])
    assert [r.target_path for r in records] == ["split:core/broken.py"]
    assert "unparseable" in records[0].problem_quote


def test_a_small_module_is_not_flagged() -> None:
    records, _ = oversized_module_candidates(
        [("core/small.py", _module(code_lines=50, prose_lines=50))]
    )
    assert records == []
