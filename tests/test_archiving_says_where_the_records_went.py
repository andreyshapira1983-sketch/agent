"""Архивация обязана сказать, что прочесть архив нечем.

СВЕРКА С ПОЛЕМ, форма F-2 (docs/audit/FIELD_CHECK_QUEUE.md), побочный улов.

САМ ПОРОГ ПРОВЕРЕН И ДЕФЕКТА НЕ ИМЕЕТ. `DEFAULT_ARCHIVE_THRESHOLD = 0.25`
работает ровно так, как предписало правило оператора MIR-074: словесное
совпадение при вставке кредита почти не даёт, бессмертие покупает только
причинное использование. Живой замер: 16 из 778 архивированных записей были до
того прочитаны один–три раза — это и есть случай, ради которого правило
заводилось, а не его нарушение.

НАЙДЕНО ДРУГОЕ. `load_archive()` существует и не имеет НИ ОДНОГО вызывающего:
ни в агенте, ни в CLI, ни в тике. `list_persistent` читает только активную
память. Живьём: 125 записей активны, **778 в архиве** — 86 % памяти агента
лежит там, куда не смотрит ни одна команда.

Правило MIR-074 гласило «пока не пригодилось — отодвинем подальше», dormant,
not destroyed. Сон осмыслен, только если из него можно разбудить. Читателя нет,
и потому «подальше» на деле означает «насовсем».

ЧТО ЗДЕСЬ ДЕЛАЕТСЯ. Не читатель — это машинерия, и класс «хранилище с писателем
и без читателя» уже зарегистрирован как MIR-138, открытый решением оператора.
Здесь закрывается только умолчание: строка, сообщающая об архивации, обязана
назвать цену — записи ушли туда, откуда их не достанет ни одна команда.
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


def test_nothing_reads_the_archive_and_the_message_may_stop_saying_so() -> None:
    """Растяжка: как только читатель появится, надпись обязана устареть.

    По РАЗБОРУ, а не по тексту. Первая редакция искала подстроку и покраснела
    на собственном комментарии, объясняющем починку, — проба, меряющая текст,
    меряет адрес, а не свойство. Этот урок за сегодня уже был.
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

    assert not callers, (
        "у архива появился читатель — сообщение команды больше не верно, "
        f"перепишите его и эту растяжку: {callers}"
    )
