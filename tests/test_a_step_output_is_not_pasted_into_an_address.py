"""Вклеенный внутрь адреса вывод шага — одно имя, а не весь вывод.

След logs/trace_965c1af5ab42055b85840ae36e47c5aa.jsonl (ночь 20→21.09): в
`math_study/library/txt/{{step:3.output}}` подставился весь вывод шага —
кусок чужого файла, десяток имён через перевод строки — и file_read искал
такой «файл». Дефект нашёл сам агент (разговор 21.09 «подстановка пути»);
его собственное предложение правки тем же дефектом и испортилось.
"""
from __future__ import annotations

import pytest

from core.step_references import UnresolvedStepReference, resolve_step_references


def _cmd(stdout: str) -> dict:
    return {"code": "print()", "stdout": stdout, "stderr": "", "exit_code": 0,
            "timed_out": False, "stdout_truncated": False}


def test_a_multi_line_output_is_refused_inside_a_path() -> None:
    with pytest.raises(UnresolvedStepReference, match="address"):
        resolve_step_references(
            {"path": "math_study/library/txt/{{step:3.output}}"},
            {"3": _cmd("a.txt\nb.txt\nc.txt\n")})


def test_a_list_is_refused_inside_a_url() -> None:
    with pytest.raises(UnresolvedStepReference, match="list"):
        resolve_step_references({"url": "https://x.org/{{step:1.output}}"}, {"1": ["a", "b"]})


def test_one_name_goes_into_a_path_without_its_newline() -> None:
    got = resolve_step_references({"path": "notes/{{step:1.output}}.md"}, {"1": _cmd("report\n")})
    assert got == {"path": "notes/report.md"}


def test_text_outside_an_address_still_takes_the_whole_output() -> None:
    got = resolve_step_references({"content": "Итог:\n{{step:1.output}}"}, {"1": _cmd("1\n2\n")})
    assert got == {"content": "Итог:\n1\n2\n"}


def test_a_whole_argument_reference_is_untouched() -> None:
    assert resolve_step_references({"path": "{{step:1.output}}"}, {"1": ["a"]}) == {"path": ["a"]}
