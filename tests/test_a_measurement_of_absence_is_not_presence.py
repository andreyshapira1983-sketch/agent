"""Улика эксперимента — его ИСХОД: эхо вопроса не опровергает ответ мира.

Background: docs/CODE_NOTES.md, "The experiment's question refuted its answer".
"""
from __future__ import annotations

from core.evidence import evidence_from_tool_result
from core.verifier_absence import absence_reason, absence_refuted_by_excerpt

#: Живая проба №4 (2026-08-16, trace_471e5543): эксперимент напечатал
#: `has batched: False`, ответ заключил «itertools.batched отсутствует» — и
#: гейт отсутствия опроверг его СОБСТВЕННОЙ уликой: в выдержку попал весь
#: словарь вывода, включая КОД эксперимента, где имя стоит под hasattr-защитой
#: (`print('batched:', itertools.batched)`). Имя жило в вопросе, гейт прочёл
#: его как ответ.
_LIVE_CODE = (
    "import itertools, inspect\n"
    "print('python version ok')\n"
    "print('has batched:', hasattr(itertools, 'batched'))\n"
    "if hasattr(itertools, 'batched'):\n"
    "    print('signature:', inspect.signature(itertools.batched))\n"
)
_LIVE_OUTPUT = {
    "code": _LIVE_CODE,
    "exit_code": 0,
    "stdout": "python version ok\nhas batched: False\n",
    "stderr": "",
    "stdout_truncated": False,
    "stderr_truncated": False,
    "duration_ms": 120,
    "timed_out": False,
}
_LIVE_CLAIM = (
    "Однако в твоей среде выполнения этот атрибут отсутствует, поэтому код "
    "с `itertools.batched` здесь работать не будет."
)


def _probe_evidence():
    got = evidence_from_tool_result(
        tool_name="python_probe",
        arguments={"code": _LIVE_CODE},
        output=_LIVE_OUTPUT,
    )
    (ev,) = [e for e in (got if isinstance(got, list) else [got]) if e is not None]
    return ev


def test_the_excerpt_is_the_outcome_not_the_question():
    ev = _probe_evidence()

    assert "has batched: False" in (ev.excerpt or "")
    assert "itertools.batched" not in (ev.excerpt or ""), (
        "код эксперимента — вопрос; в выдержке улики ему не место"
    )


def test_the_measured_absence_is_not_refuted_by_its_own_question():
    ev = _probe_evidence()

    assert absence_reason(_LIVE_CLAIM, ev, "tool") is None


def test_real_presence_still_refutes_absence():
    """Улов не отдан: улика, где вещь ЖИВЁТ (сигнатура в документации),
    опровергает «её нет», как и раньше.
    """
    docs = "itertools.batched(iterable, n, strict=False)\nAdded in version 3.12."
    assert absence_refuted_by_excerpt(_LIVE_CLAIM, docs) is True


def test_stderr_still_reaches_the_excerpt():
    """Ошибка — это тоже ответ мира: ImportError обязан остаться уликой."""
    output = dict(_LIVE_OUTPUT, exit_code=1, stdout="",
                  stderr="ImportError: cannot import name 'batched'")
    got = evidence_from_tool_result(
        tool_name="python_probe", arguments={"code": "from itertools import batched"},
        output=output,
    )
    (ev,) = [e for e in (got if isinstance(got, list) else [got]) if e is not None]

    assert "ImportError" in (ev.excerpt or "")
