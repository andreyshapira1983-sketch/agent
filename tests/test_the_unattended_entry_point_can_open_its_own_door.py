"""Дверь безнадзорной работы открывается там, где агент работает.

Замер живого прогона 2026-09-18: 26 циклов, и каждый из них — `blocked`.
Причина одна и та же, словами самого рантайма (`core/autonomous_runtime.py`,
ветка «no permission»): `approval required`. В ящике 13 заявок — двенадцать
раз автомат просил права на эффекты и ни разу не получил ответа: восемь
просрочены, четыре висят до сих пор.

И ни одной заявки `autonomous_runtime.standing_grant` за всю историю ящика.
Механизм безнадзорной работы рантайм ЧИТАЛ (`active_standing_grant`), а
завести его можно было ровно одним способом — командой `:standing-grant` в
интерактивном `main.py`. Планировщик задач в REPL не печатает, а расписание
и есть тот случай, ради которого стоячий грант придуман. У пути автономии не
было своей двери: читатель без достижимого писателя, зеркало MIR-138.

Правило B (MIR-117) здесь не отменяется, а исполняется по существу. Просьба и
разрешение остаются ДВУМЯ записями, и каждая названа по автору — ровно так,
как это уже делает `core/rule_approved_apply.py`, где разрешение пишет не
человек, а правило под своим именем. Что здесь ново — автором названа
командная строка оператора, и «unattributed» она не пишет никогда.

Уступка названа вслух: одним действием оператор проходит оба своих шага.
Защищает не церемония из двух нажатий, а границы — числа прогонов и часов
обязательны, риск объявлен, срок конечен, обе записи лежат в долговечном
ящике. Флаг живёт в разборе аргументов и коду агента недостижим.
"""
from __future__ import annotations

import io
from pathlib import Path

import pytest

from core.approval_inbox import DEFAULT_APPROVAL_INBOX_PATH, ApprovalInbox
from core.autonomous_runtime import active_standing_grant
from core.standing_grant import (
    COMMAND_LINE_ACTOR,
    STANDING_GRANT_OPERATION,
    file_standing_grant,
    open_grant_from_command_line,
    open_standing_grant,
)

_ACTOR = COMMAND_LINE_ACTOR


def _inbox(workspace: Path) -> ApprovalInbox:
    return ApprovalInbox(path=workspace / DEFAULT_APPROVAL_INBOX_PATH)


def test_the_tick_opens_a_grant_the_runtime_itself_accepts(tmp_path: Path) -> None:
    """Свидетель: грант, открытый из точки входа, признаёт САМ рантайм.

    Спрашивается той же функцией, которой спрашивает боевой путь, — своя
    проверка разошлась бы с настоящей и доказывала бы собственную выдумку
    (класс H-26 проспективного аудита).
    """
    opened = open_standing_grant(
        tmp_path, runs_per_day=6, hours=48, actor=_ACTOR,
    )

    live = active_standing_grant(_inbox(tmp_path), tmp_path)
    assert live is not None, "рантайм не видит гранта: дверь открыта не туда"
    assert live.id == opened.id


def test_the_request_and_the_verdict_stay_two_records(tmp_path: Path) -> None:
    """Правило B: просьба и разрешение — два события, а не одно.

    Слить их в одну запись значило бы, что грант появился без просьбы: в
    ящике не осталось бы следа, ЧТО именно разрешали и на каких границах.
    """
    item = open_standing_grant(
        tmp_path, runs_per_day=6, hours=48, actor=_ACTOR,
    )

    assert item.status == "approved"
    assert item.operation == STANDING_GRANT_OPERATION
    # Просьба пережила разрешение: границы читаются из той же записи.
    assert (item.payload or {}).get("max_runs_per_day") == 6
    assert item.risk == "irreversible"
    assert item.expires_at


def test_the_verdict_names_its_author_and_never_says_unattributed(
    tmp_path: Path,
) -> None:
    """Разрешение без автора неотличимо от разрешения, которого не давали.

    `ApprovalInbox._verdict` пишет «unattributed», когда автор не назван;
    именно это слово стоит во всех тринадцати заявках живого ящика.
    """
    item = open_standing_grant(
        tmp_path, runs_per_day=6, hours=48, actor=_ACTOR,
    )

    assert item.decided_by == _ACTOR
    assert item.decided_by != "unattributed"
    assert item.decision_reason, "молчаливое «да» не объясняет себя"


def test_a_grant_without_bounds_is_refused(tmp_path: Path) -> None:
    """Границы обязательны, и отказ не оставляет мёртвой заявки.

    Ноль прогонов рантайм читает как «гранта нет», нулевой срок — как
    истёкший. Завести такую запись молча хуже, чем отказать: ящик показывал
    бы право, которого нет.
    """
    for runs, hours in ((0, 48), (6, 0), (-1, 48)):
        with pytest.raises(ValueError):
            open_standing_grant(
                tmp_path, runs_per_day=runs, hours=hours, actor=_ACTOR,
            )

    filed = [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ]
    assert not filed, "отказ всё же положил заявку"


def test_filing_alone_leaves_the_grant_shut(tmp_path: Path) -> None:
    """Контроль: писатель сам по себе НЕ открывает — открывает вердикт.

    Если бы заявка становилась правом от одного факта своего появления,
    свидетель выше был бы зелёным и без разрешения, то есть не проверял бы
    ничего.
    """
    file_standing_grant(
        _inbox(tmp_path), runs_per_day=6, hours=48,
        requested_by="operator:agent_tick --standing-grant",
    )

    assert active_standing_grant(_inbox(tmp_path), tmp_path) is None


def test_the_interactive_command_still_only_files(tmp_path: Path) -> None:
    """Контроль: старая дверь не поменяла смысла.

    `:standing-grant` в REPL по-прежнему КЛАДЁТ заявку и ждёт отдельного
    слова. Новый флаг добавляет путь, а не переписывает прежний.
    """
    from cli.command_dispatch import handle_meta_command

    class _Log:
        def log(self, *args, **kwargs) -> None:
            return None

    class _Agent:
        def __init__(self) -> None:
            self.approval_inbox = _inbox(tmp_path)
            self.log = _Log()

    handle_meta_command(":standing-grant 6 48", _Agent(), tmp_path)

    filed = [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ]
    assert filed and filed[0].status == "pending"
    assert active_standing_grant(_inbox(tmp_path), tmp_path) is None


def test_both_doors_file_through_one_writer(tmp_path: Path) -> None:
    """Контроль: две двери — один писатель, иначе они разойдутся.

    Рантайм ищет грант по одной операции и одному ключу границ. Второй
    писатель со своей формой записи дал бы заявку, которую читатель молча
    не узнаёт, — та же болезнь, от которой вынесен `active_standing_grant`.
    """
    from cli import commands_approval

    assert commands_approval.STANDING_GRANT_OPERATION is STANDING_GRANT_OPERATION


def test_the_entry_point_offers_the_flag(monkeypatch) -> None:
    """Свидетель: флаг есть там, где запускается расписание.

    До этой правки грант заводился только командой в интерактивном
    ``main.py``. Планировщик задач в REPL не печатает, и безнадзорный путь
    упирался в ворота, ключ от которых лежал за другой дверью.
    """
    import agent_tick

    monkeypatch.setattr(
        "sys.argv", ["agent_tick.py", "--standing-grant", "20", "48"],
    )
    args = agent_tick._parse_args()

    assert args.standing_grant == ["20", "48"]


def test_the_flag_opens_the_grant_and_says_what_it_opened(tmp_path: Path) -> None:
    """Свидетель: флаг открывает право и называет его границы.

    Молчаливое «готово» не годится: оператор выдаёт необратимое
    право и обязан увидеть, сколько прогонов, докакого часа и чьей
    подписью оно открыто.
    """
    out = io.StringIO()

    code = open_grant_from_command_line(tmp_path, "20", "48", out=out)

    assert code == 0
    live = active_standing_grant(_inbox(tmp_path), tmp_path)
    assert live is not None, "флаг отчитался об успехе, а рантайм гранта не видит"
    printed = out.getvalue()
    assert "20" in printed
    assert str(live.expires_at) in printed
    assert COMMAND_LINE_ACTOR in printed


def test_bad_bounds_return_a_nonzero_code_and_file_nothing(tmp_path: Path) -> None:
    """Контроль: негодные границы не становятся тихим нулём.

    Нулевой код возврата в расписании читается как «грант есть», и
    следующий тик упирался бы в ворота без всякого объяснения.
    """
    out = io.StringIO()

    code = open_grant_from_command_line(tmp_path, "0", "48", out=out)

    assert code != 0
    assert not [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ]
    assert out.getvalue().strip(), "отказ промолчал"
