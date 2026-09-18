"""Кампания судит СВОЮ цель её же критерием — и записывает вердикт.

Зачем этот файл. `core/success_check.py` умеет читать мир и говорить, сошёлся
ли критерий, с 2026-09-17. Он подключён к ОДИНОЧНОЙ задаче
(`core/autonomous_runtime.py`, строка с `observe_success_check`) — и не
подключён к кампании ни разу. `core/campaign.py` тащит `success_check` через
каждую строку реестра и ни разу по нему не судит. Поэтому слова «достигнуто»
в этом репозитории не существует нигде: страж повторов
(`charter_goal._recent_goals`) вынужден спрашивать «была ли работа», потому
что спросить «сделано ли дело» физически не у чего.

Замер, который родил этот файл (живой реестр владельца, 232 строки, 31
несовпадающий критерий): 63 строки вовсе без критерия; из 31 критерия 27 дают
`unverifiable` — критерий не называет наблюдаемого следа; 4 дают `verified`,
и ВСЕ ЧЕТЫРЕ ложны. Критерий гласил «существует проверенная заявка,
перечисляющая, что выделить из core/smart_memory.py». Судья нашёл в мире
`core/smart_memory.py` и сказал «сошлось». Заявок в той кампании ноль.
Судья спутал ПРЕДМЕТ разговора с ПРОДУКТОМ работы.

Отсюда моя собственная ошибка, названная вслух: я собирался подключить судью
как есть, одной строкой в конце `run_campaign`. Это записало бы в вечный
журнал четыре ложных «достигнуто» — ровно ту болезнь «event ≠ outcome»,
против которой написана половина этого репозитория. Спасло правило дома:
замерить до правки.

Поэтому вердиктов четыре, а не три, и четвёртый назван словом:

* ``verified``     — все названные следы найдены И хотя бы один из них моложе
                     начала кампании: работа этого прогона видна в мире;
* ``preexisting``  — все следы найдены, но все старше начала кампании. Это НЕ
                     успех: файл, лежавший здесь до прогона, ничего о прогоне
                     не свидетельствует;
* ``missing``      — хотя бы один названный след не найден;
* ``unverifiable`` — критерий не называет наблюдаемого следа (или критерия
                     не назвали вовсе — четыре точки входа задают цель строкой).

Страж повторов здесь НЕ меняется. Сначала факт должен существовать и быть
проверяемым; кто на него обопрётся — отдельный разговор и отдельная правка.
"""
from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

from core.campaign import run_campaign
from core.campaign_ledger import CampaignLedger
from core.campaign_types import CampaignActionOutcome, CampaignConfig
from core.campaign_verdict import VERDICT_RELPATH, judge_and_record

_START = datetime(2026, 9, 17, 12, 0, tzinfo=timezone.utc)
_GOAL = "write the note that names the split boundary"


def _gather(agent, workspace, approval_inbox, goal="", exhausted_actions=frozenset()):
    return {"action": SimpleNamespace(
        action="propose_engineering_task", title="one thing", severity="low",
        priority=10, risk="reversible", grounds="operator_goal",
        decided_by="test", next_check_at=None, reason="",
    )}


def _run(tmp_path: Path, check: str, *, writes: str = "", cycles: int = 1):
    """Прогнать кампанию, опционально дав исполнителю создать след."""

    def _execute(*, agent, workspace, action, config, approval_inbox=None):
        if writes:
            target = Path(workspace) / writes
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text("сделано\n", encoding="utf-8")
        return CampaignActionOutcome(result="completed", work_done=True,
                                     subject="s", llm_calls_spent=1)

    return run_campaign(
        CampaignConfig(goal=_GOAL, success_check=check,
                       max_cycles=cycles, dry_run=False),
        agent=SimpleNamespace(log=None),
        workspace=str(tmp_path),
        gather_signals=_gather,
        execute_action=_execute,
        ledger=CampaignLedger(path=tmp_path / "data" / "campaign_ledger.jsonl"),
        now_fn=lambda: _START,
    )


def _verdict_rows(tmp_path: Path) -> list[dict]:
    path = tmp_path / VERDICT_RELPATH
    if not path.is_file():
        return []
    rows = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        row = json.loads(line)
        rows.append(row.get("payload") if isinstance(row.get("payload"), dict) else row)
    return rows


def _age(path: Path, *, years_back: int = 1) -> None:
    """Состарить след: он существовал ДО кампании."""
    old = _START.replace(year=_START.year - years_back).timestamp()
    os.utime(path, (old, old))


def test_a_finished_campaign_writes_down_whether_it_achieved_its_goal(tmp_path: Path) -> None:
    """Свидетель: у прогона появляется запись «сошлось или нет».

    До этой правки такой записи не было нигде: ни в реестре циклов, ни в
    журнале агента. Спросить систему «достигла ли кампания своей цели» было
    не у чего — можно было спросить только «сколько циклов что-то делали».
    """
    result = _run(tmp_path, "заметка лежит в docs/result.md", writes="docs/result.md")

    rows = _verdict_rows(tmp_path)
    assert len(rows) == 1, (
        "кампания обязана оставить ровно один вердикт о своей цели; "
        f"оставлено {len(rows)}"
    )
    row = rows[0]
    assert row["goal"] == _GOAL, "вердикт без цели нельзя привязать к теме"
    assert row["success_check"] == "заметка лежит в docs/result.md", (
        "критерий кладётся ДОСЛОВНО: иначе вердикт нельзя перепроверить"
    )
    assert row["stop_reason"] == result.stop_reason


def test_a_trace_made_during_the_run_counts_as_achieved(tmp_path: Path) -> None:
    """Свидетель: след, появившийся В ПРОГОНЕ, — это «сошлось»."""
    result = _run(tmp_path, "заметка лежит в docs/result.md", writes="docs/result.md")

    assert (tmp_path / "docs" / "result.md").is_file(), "исполнитель обязан был создать след"
    assert result.success_verdict["verdict"] == "verified"
    assert "docs/result.md" in result.success_verdict["fresh_traces"]
    assert _verdict_rows(tmp_path)[0]["verdict"] == "verified"


def test_a_trace_older_than_the_run_is_not_called_achieved(tmp_path: Path) -> None:
    """Свидетель против ЛОЖНОГО «сошлось» — того самого, что дал живой замер.

    Четыре вердикта `verified` из 31 живого критерия были ложны одинаково:
    критерий называл `core/smart_memory.py` как ПРЕДМЕТ будущей заявки, судья
    находил этот файл в мире (он там лежит годами) и объявлял цель
    достигнутой. Заявок в той кампании было ноль.

    Разделитель прост и наблюдаем: след, который старше начала кампании,
    ничего об этой кампании не свидетельствует.
    """
    subject = tmp_path / "core" / "smart_memory.py"
    subject.parent.mkdir(parents=True, exist_ok=True)
    subject.write_text("# предмет разговора, а не продукт работы\n", encoding="utf-8")
    _age(subject)

    result = _run(tmp_path, "заявка перечисляет, что выделить из core/smart_memory.py")

    assert result.success_verdict["verdict"] == "preexisting", (
        "файл, лежавший здесь до прогона, не доказывает работу прогона"
    )
    assert result.success_verdict["fresh_traces"] == []
    assert "core/smart_memory.py" in result.success_verdict["named_traces"]
    assert _verdict_rows(tmp_path)[0]["verdict"] == "preexisting"


def test_a_named_trace_that_never_appeared_is_missing(tmp_path: Path) -> None:
    """Свидетель: названный и не появившийся след — это «не сошлось»."""
    result = _run(tmp_path, "заметка лежит в docs/result.md")

    assert result.success_verdict["verdict"] == "missing"
    assert "docs/result.md" in result.success_verdict["missing_traces"]


def test_a_criterion_naming_no_trace_is_unverifiable_not_success(tmp_path: Path) -> None:
    """Свидетель: «заявка рассмотрена и одобрена» — это НЕ проверка.

    Так выглядят 27 из 31 живого критерия. Незнание, выданное за проверку,
    хуже отсутствия проверки, поэтому у него своё слово.
    """
    result = _run(tmp_path, "The proposal is reviewed and approved by peers.")

    assert result.success_verdict["verdict"] == "unverifiable"
    assert result.success_verdict["named_traces"] == []


def test_a_goal_that_never_named_a_criterion_still_leaves_a_verdict(tmp_path: Path) -> None:
    """Свидетель: молчание тоже записывается, и названо честно.

    Четыре точки входа задают цель строкой без всякого критерия — 63 строки
    живого реестра пришли без него. Не записать вердикт значило бы, что
    молчание и проверка выглядят одинаково: никак.
    """
    result = _run(tmp_path, "")

    assert result.success_verdict["verdict"] == "unverifiable"
    assert "критерий не назван" in result.success_verdict["reason"], (
        "непоставленный критерий обязан отличаться от поставленного и "
        f"непроверяемого; сказано: {result.success_verdict['reason']!r}"
    )
    assert _verdict_rows(tmp_path)[0]["verdict"] == "unverifiable"


def test_the_verdict_reaches_the_human_summary(tmp_path: Path) -> None:
    """Свидетель: человек видит вердикт в той же сводке, что и циклы.

    Сводка печатала `status=completed` для прогона, который не достиг цели.
    Формально верно — «смена отработана», — а человеку читается как успех.
    """
    result = _run(tmp_path, "заметка лежит в docs/result.md")

    summary = result.user_summary()
    assert "missing" in summary, f"вердикт не доехал до человека: {summary.splitlines()[:3]}"
    assert "docs/result.md" in summary, "человеку не назван след, которого не хватило"


def test_the_verdict_is_judged_by_the_world_not_by_the_cycle_words(tmp_path: Path) -> None:
    """Контроль: слово исполнителя «completed, work_done» вердикта НЕ решает.

    Иначе судья стал бы эхом исполнителя: ровно так «я создал файл» и
    созданный файл были для системы одним событием до 2026-09-17.
    """
    result = _run(tmp_path, "заметка лежит в docs/result.md", cycles=3)

    assert result.totals["useful_cycles"] >= 1, "циклы обязаны были отчитаться о работе"
    assert result.success_verdict["verdict"] == "missing", (
        "мир пуст — значит не сошлось, сколько бы циклов ни сказали «готово»"
    )


def test_the_verdict_does_not_pollute_the_cycle_ledger(tmp_path: Path) -> None:
    """Контроль: у реестра циклов прежние читатели, и строка не цикла их сломает.

    `_recent_goals`, `spent_units_by_action` и `summarise_ledger` читают
    `campaign_ledger.jsonl` как «одна строка = один цикл». Вердикт живёт в
    своём журнале именно поэтому.
    """
    result = _run(tmp_path, "заметка лежит в docs/result.md", cycles=2)

    rows = (tmp_path / "data" / "campaign_ledger.jsonl").read_text(
        encoding="utf-8").splitlines()
    assert len(rows) == result.cycles_run, (
        f"в реестре циклов {len(rows)} строк при {result.cycles_run} циклах"
    )
    for line in rows:
        assert "verdict" not in json.loads(line), "вердикт просочился в строку цикла"


def test_an_unwritable_journal_does_not_swallow_the_failure(tmp_path: Path) -> None:
    """Контроль: незаписанный вердикт не валит прогон, но и не молчит.

    Работа кампании уже сделана — терять её из-за недоступного файла хуже,
    чем потерять вердикт. Но проглоченная ошибка записи выглядит ровно как
    успешная запись, а значит «вердикта нет» станет неотличимо от «вердикт
    записан»: та же болезнь молчания, что и всё в этом файле.
    """
    blocker = tmp_path / "not-a-directory"
    blocker.write_text("я файл, а не папка\n", encoding="utf-8")

    verdict, error = judge_and_record(
        goal=_GOAL, success_check="", workspace=blocker,
        started_at=_START, ts=_START,
    )

    assert verdict["verdict"] == "unverifiable", "судья обязан отсудить и без журнала"
    assert error, "ошибка записи обязана дойти наружу, а не исчезнуть"
