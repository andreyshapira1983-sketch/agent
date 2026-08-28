"""Архивация обязана сказать, ЧЕМ прочесть архив (читатель построен, MIR-138).

Замер, отвергнутые варианты и границы: F-2 в docs/audit/FIELD_CHECK_QUEUE.md, MIR-074 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import pathlib


def test_the_archive_command_names_the_price(capsys, monkeypatch) -> None:
    import cli.commands_memory as mod

    class _Report:
        scanned = 40
        archived = ("m1", "m2", "m3")
        threshold = 0.25
        min_age_days = 7

    class _Agent:
        def archive_persistent(self, **_kw):
            return _Report()

    handled = mod._handle_hygiene("archive", _Agent(), pathlib.Path("."))
    printed = capsys.readouterr().err

    assert handled
    assert "3" in printed, printed
    assert "persistent_memory.archive.jsonl" in printed, (
        "команда говорит «архивировано», не называя, где записи и чем их "
        "прочесть — а прочесть их нечем:\n" + printed
    )


def test_the_archive_reader_set_is_exactly_the_sanctioned_door() -> None:
    """Растяжка переехала ПО СОБСТВЕННОЙ инструкции (2026-08-28): читатель
    появился (`search_archive` → `:smart-memory archive`, MIR-138), и теперь
    закон обратный — читает РОВНО санкционированная дверь. Второй вызывающий
    `load_archive` вне неё — красный: у чтения архива один рот, как у
    выдвижения гипотез.

    По РАЗБОРУ, а не по тексту (урок первой редакции сохранён).
    """
    import ast

    repo = pathlib.Path(__file__).resolve().parent.parent
    targets = (
        list(repo.glob("core/*.py")) + list(repo.glob("cli/*.py"))
        + list(repo.glob("app/*.py")) + [repo / "agent_tick.py"]
    )
    callers: list[str] = []
    for path in targets:
        if not path.exists() or path.name == "persistent_memory.py":
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:
            continue
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Attribute)
                and node.func.attr == "load_archive"
            ):
                callers.append(f"{path.name}:{node.lineno}")

    sanctioned = {"commands_memory.py"}
    strangers = [c for c in callers if c.split(":")[0] not in sanctioned]
    assert not strangers, (
        f"у архива появился ВТОРОЙ читатель вне санкционированной двери: {strangers}"
    )
