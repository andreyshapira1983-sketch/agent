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


def test_the_request_and_the_verdict_stay_two_records(
    tmp_path: Path, monkeypatch,
) -> None:
    """Правило B: просьба и разрешение — два события, а не одно.

    Прежняя редакция этого свидетеля проверяла только КОНЕЧНУЮ запись, и
    ломка это доказала: когда событие просьбы вырезали, а элемент рождался
    сразу разрешённым, весь файл оставался зелёным. `set_status` правит
    единственную запись на месте, поэтому по итогу не видно, был ли шаг
    просьбы вообще. Смотреть надо на сам проход: заявку кладёт писатель, и
    в момент укладки она `pending`.

    Квитанции тут не помогут: `core/approval_inbox.py` шлёт квитанцию на
    `approval_inbox.add`, а на разрешение НЕ шлёт никакой — отдельного
    события `approval_inbox.approve` в проекте не существует.
    """
    import core.standing_grant as sg

    filed: list[tuple] = []
    real = sg.file_standing_grant

    def _spy(inbox, **kwargs):
        item = real(inbox, **kwargs)
        filed.append((item.id, item.status, kwargs["runs_per_day"], kwargs["hours"]))
        return item

    monkeypatch.setattr(sg, "file_standing_grant", _spy)

    item = sg.open_standing_grant(
        tmp_path, runs_per_day=6, hours=48, actor=_ACTOR,
    )

    assert len(filed) == 1, "право появилось, минуя событие просьбы"
    filed_id, filed_status, runs, hours = filed[0]
    assert filed_status == "pending", "заявка родилась уже разрешённой"
    assert (int(runs), int(hours)) == (6, 48)
    assert item.id == filed_id, "разрешили не ту запись, которую просили"

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


def test_both_doors_file_through_one_writer(tmp_path: Path, monkeypatch) -> None:
    """Контроль: две двери — один писатель, иначе они разойдутся.

    Рантайм ищет грант по одной операции и одному ключу границ. Второй
    писатель со своей формой записи дал бы заявку, которую читатель молча
    не узнаёт, — та же болезнь, от которой вынесен `active_standing_grant`.

    Прежняя редакция сравнивала только пере-экспортированную строку, и
    ломка это доказала: REPL-двери приписали свой встроенный писатель с
    ключом `runs` вместо `max_runs_per_day`, константу оставили на месте —
    контроль остался зелёным. То есть он пропускал ровно то расхождение,
    от которого поставлен. Теперь проверяется, что общий писатель РАБОТАЛ
    и что в записи лежит ключ, который читает рантайм.
    """
    from cli import commands_approval

    seen: list[dict] = []
    real = commands_approval.file_standing_grant

    def _spy(inbox, **kwargs):
        seen.append(dict(kwargs))
        return real(inbox, **kwargs)

    monkeypatch.setattr(commands_approval, "file_standing_grant", _spy)

    class _Log:
        def log(self, *args, **kwargs) -> None:
            return None

    class _Agent:
        def __init__(self) -> None:
            self.approval_inbox = _inbox(tmp_path)
            self.log = _Log()

    from cli.command_dispatch import handle_meta_command

    handle_meta_command(":standing-grant 6 48", _Agent(), tmp_path)

    assert seen, "REPL-дверь завела заявку мимо общего писателя"
    assert int(seen[0]["runs_per_day"]) == 6
    assert int(seen[0]["hours"]) == 48

    filed = [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ]
    assert filed, "запись второй двери не попала в ящик"
    assert (filed[0].payload or {}).get("max_runs_per_day") == 6, (
        "вторая дверь записала границы не тем ключом, который читает рантайм"
    )


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
    право и обязан увидеть, сколько прогонов, до какого часа и чьей
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


def test_the_grant_writer_stands_behind_both_fences() -> None:
    """Свидетель: новый орган власти огорожен, как его родня.

    Этот модуль превращает флаг оператора в действующее право. Пока он
    не за забором, безнадзорная полоса вправе править ЕГО — то есть сама
    себе переписать, кто и на каких границах получает разрешение. Рядом
    стоящие `core/approval_inbox.py` и `core/rule_approved_apply.py`
    огорожены обоими заборами именно поэтому; поставить третий орган
    власти рядом и забор ему не завести — дыра, а не экономия.
    """
    from core.burn_in_sandbox import _FENCE
    from core.burn_in_supervisor import SUPERVISOR_FENCE

    kin = ("core/approval_inbox.py", "core/rule_approved_apply.py")
    for name, fence in (("песочница", _FENCE), ("принимающий", SUPERVISOR_FENCE)):
        for relative in kin:
            assert relative in fence, f"{name}: родня разогорожена, мерка врёт"
        assert "core/standing_grant.py" in fence, (
            f"{name}: писатель права не огорожен — полоса правит сама себе право"
        )


def test_the_request_names_the_operator_not_the_automaton(tmp_path: Path) -> None:
    """Свидетель: просьбу подписывает тот, кто её подал.

    Вердикт называл автора и раньше, а просьба — нет: `ApprovalInbox.add`
    не принимал `requested_by`, и запись доставалась умолчанию
    `autonomous_runtime`. В ящике выходило, что право себе просил АВТОМАТ,
    хотя флаг набрал человек. Защита тут держится на атрибуции, значит
    ложная атрибуция — это дыра в самой защите, а не косметика.
    """
    item = open_standing_grant(
        tmp_path, runs_per_day=6, hours=48, actor=_ACTOR,
    )

    assert item.requested_by == _ACTOR
    assert item.requested_by != "autonomous_runtime", (
        "ящик говорит, что право себе просил автомат"
    )
    # Подпись обязана пережить перезапуск, а не жить в памяти прогона.
    reread = [
        i for i in _inbox(tmp_path).list() if i.id == item.id
    ]
    assert reread and reread[0].requested_by == _ACTOR


def test_an_unsigned_verdict_is_refused_before_anything_is_filed(
    tmp_path: Path,
) -> None:
    """Контроль: без автора не открывается и не кладётся.

    `ApprovalInbox._verdict` превращает пустого автора в «unattributed».
    Докстринг писателя обещает, что этого слова тут не будет никогда, —
    значит обещание должен держать код, а не вежливость вызывающего.
    Отказ обязан случиться ДО укладки: иначе в ящике осталась бы висеть
    заявка, которую никто не открывал и никто не отзовёт.
    """
    for actor in ("", "   "):
        with pytest.raises(ValueError):
            open_standing_grant(
                tmp_path, runs_per_day=6, hours=48, actor=actor,
            )

    assert not [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ], "отказ по автору всё же оставил заявку"
    assert active_standing_grant(_inbox(tmp_path), tmp_path) is None


def test_extra_bounds_are_refused_instead_of_silently_dropped(
    tmp_path: Path,
) -> None:
    """Контроль: лишнее число — ошибка оператора, а не мусор под ковёр.

    `nargs="+"` принимает сколько угодно значений, а срез `[:2]` молча
    выбрасывал хвост. `--standing-grant 20 48 999` завершался нулём и
    открывал СОВСЕМ ДРУГОЕ право, чем набрал человек, и тот об этом не
    узнавал. Необратимое право не место для догадок о намерении.
    """
    out = io.StringIO()

    code = open_grant_from_command_line(tmp_path, "20", "48", "999", out=out)

    assert code != 0, "лишнее число молча проглочено"
    assert not [
        i for i in _inbox(tmp_path).list()
        if i.operation == STANDING_GRANT_OPERATION
    ]
    assert out.getvalue().strip(), "отказ промолчал"
