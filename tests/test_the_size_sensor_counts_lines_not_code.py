"""MIR-099: the oversized-module sensor cannot tell an organ from its
explanation. Pins CURRENT behaviour on purpose — the operator ruled the
sensor stays untouched until total LOC is shown to correlate with what we
mean by "oversized". Measurement and consequences: MIR-099.
"""
from __future__ import annotations

from core.backlog_signals import oversized_module_candidates


def _module(code_lines: int, prose_lines: int) -> str:
    body = ["# explanation" for _ in range(prose_lines)]
    body += [f"x{i} = {i}" for i in range(code_lines)]
    return "\n".join(body)


def test_a_module_that_is_mostly_prose_is_flagged() -> None:
    records, _ = oversized_module_candidates(
        [("core/mostly_prose.py", _module(code_lines=100, prose_lines=800))]
    )
    assert [r.target_path for r in records] == ["split:core/mostly_prose.py"]


def test_a_module_of_the_same_size_that_is_all_code_is_flagged_alike() -> None:
    """Same verdict, same quote shape: the two are indistinguishable to it."""
    prose_records, _ = oversized_module_candidates(
        [("core/a.py", _module(code_lines=100, prose_lines=800))]
    )
    code_records, _ = oversized_module_candidates(
        [("core/a.py", _module(code_lines=900, prose_lines=0))]
    )
    assert prose_records[0].problem_quote == code_records[0].problem_quote


def test_a_small_module_is_not_flagged() -> None:
    records, _ = oversized_module_candidates(
        [("core/small.py", _module(code_lines=50, prose_lines=50))]
    )
    assert records == []
