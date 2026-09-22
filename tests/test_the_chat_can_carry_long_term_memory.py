"""Разговор может нести долгую память — по ключу.

Оператор 2026-09-22: уроки поломок пишутся в долгую память, а разговорный
запуск `main.py --ask` собирал агента с `with_persistent=False` — за день 223
события «persistent store not configured», уроки в чат не доходили.
Без ключа поведение прежнее (замороженный договор
tests/characterization/test_cli_one_shot_policy.py).
"""
from __future__ import annotations

from cli.args import build_parser
from cli.one_shot import run_one_shot


def _calls(tmp_path, **kwargs) -> list[dict]:
    seen: list[dict] = []

    def fake_build(workspace, **kw):
        seen.append(kw)
        raise SystemExit(0)

    try:
        run_one_shot("вопрос", workspace=tmp_path, build_agent=fake_build, **kwargs)
    except SystemExit:
        pass
    return seen


def test_without_the_key_the_chat_has_no_long_term_memory(tmp_path) -> None:
    assert _calls(tmp_path)[0]["with_persistent"] is False


def test_with_the_key_the_chat_reads_and_writes_long_term_memory(tmp_path) -> None:
    assert _calls(tmp_path, with_persistent=True)[0]["with_persistent"] is True


def test_the_key_exists_on_the_command_line() -> None:
    args = build_parser().parse_args(["--ask", "q", "--with-persistent"])
    assert args.with_persistent is True
    assert build_parser().parse_args(["--ask", "q"]).with_persistent is False
