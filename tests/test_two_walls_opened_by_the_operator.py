"""Две стены открыты словом оператора 2026-09-23 — и обе с сохранёнными краями.

Дословно: «Открыл стены, то есть журнал, чтобы он мог позвать человека… А, пип
устанавливает, чтобы мог».

Смена ПОЛНОМОЧИЙ, не починка дефекта. Обе двери закрывал сам агент своими
вердиктами; открывает их человек, и обе сохраняют края:

* установка — только из общего хранилища, только по имени, только в СВОЁ
  окружение;
* голос — с суточным потолком обращений к человеку.
"""
from __future__ import annotations

import datetime as dt
import json
import sys
from pathlib import Path

import pytest

from tools.journal_append import (
    VOICE_CALLS_PER_DAY,
    VOICE_PATH,
    JournalAppendTool,
)
from tools.shell_exec import ShellExecTool


# --------------------------------------------------------------------------
# Установка пакетов себе
# --------------------------------------------------------------------------
def _shell(tmp_path: Path) -> ShellExecTool:
    return ShellExecTool(workspace_root=tmp_path)


def test_installing_a_package_needs_no_approval(tmp_path: Path) -> None:
    """Ставить себе инструмент — сильная сторона, а не нарушение.

    Слово оператора 2026-09-23 о попытке агента поставить пакет: «он наоборот
    сделал правильно, а не писал мне сообщение каждые пять минут». Поэтому
    `reversible` (ворота пропускают с записью причины), а не `irreversible`
    (ворота ждут человека).
    """
    tool = _shell(tmp_path)
    assert tool.risk_for({"argv": ["uv", "pip", "install", "cowsay"]}) == "reversible"
    assert tool.risk_for({"argv": ["uv", "pip", "list"]}) == "read_only"


@pytest.mark.parametrize(
    "argv",
    [
        ["uv", "pip", "install", "--index-url", "http://elsewhere/", "pkg"],
        ["uv", "pip", "install", "-U", "fastapi"],
        ["uv", "pip", "install", "--target", "/tmp", "pkg"],
        ["uv", "pip", "install", "git+https://example.invalid/x/y"],
        ["uv", "pip", "install", "."],
        ["uv", "pip", "install", "/tmp/pkg.whl"],
        ["uv", "pip", "uninstall", "fastapi"],
        ["uv", "run", "python"],
        ["uv", "pip", "install"],
    ],
)
def test_the_install_door_keeps_its_edges(tmp_path: Path, argv: list[str]) -> None:
    """Открыта установка, а не произвольная команда.

    Один запрет на флаги закрывает сразу всё опасное: чужой сервер пакетов
    (`--index-url`, `--find-links`), поломку закреплённого окружения, на
    котором агент сам и работает (`--upgrade`), установку мимо окружения
    (`--target`). Адреса, пути и репозитории — не имена пакетов.
    """
    with pytest.raises(PermissionError):
        _shell(tmp_path)._validate_argv(argv)


def test_a_plain_package_name_passes(tmp_path: Path) -> None:
    tool = _shell(tmp_path)
    for argv in (
        ["uv", "pip", "install", "cowsay"],
        ["uv", "pip", "install", "pandas==2.2.0"],
        ["uv", "pip", "list"],
    ):
        assert tool._validate_argv(argv)[0] == "uv"


def test_the_install_targets_the_agents_own_interpreter(tmp_path: Path) -> None:
    """Пакет ставится в ТО окружение, на котором агент работает.

    Замер 2026-09-23: в окружении агента pip нет вовсе (оно создано uv), а
    системный pip ставит в другой Python — `pdfplumber` есть у агента и
    отсутствует в системном. Без этой подстановки агент получал бы
    «установлено» на пакет, которого потом не увидит: тихая ловушка.
    """
    seen: dict[str, list[str]] = {}

    tool = _shell(tmp_path)
    tool._run_subprocess = lambda cmd, argv, plan: seen.setdefault("argv", argv) or {}  # type: ignore[assignment]
    tool.run(["uv", "pip", "install", "cowsay"])

    argv = seen["argv"]
    assert "--python" in argv
    assert argv[argv.index("--python") + 1] == sys.executable
    assert argv[-1] == "cowsay"


# --------------------------------------------------------------------------
# Голос: суточный потолок
# --------------------------------------------------------------------------
def _voice(tmp_path: Path) -> JournalAppendTool:
    (tmp_path / "data").mkdir(exist_ok=True)
    return JournalAppendTool(workspace_root=tmp_path)


def test_the_voice_has_a_hard_daily_ceiling(tmp_path: Path) -> None:
    """Потолок открыт ВМЕСТЕ с дверью, а не после неё.

    Замеры внимания: восстановление после прерывания около 23 минут, а
    «умное» прерывание стоит столько же, сколько глупое — полезность повода
    не отменяет цены. Без потолка дверь даёт два исхода, оба плохи: он либо
    молчит, либо заваливает человека.
    """
    tool = _voice(tmp_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(VOICE_CALLS_PER_DAY):
        tool.run(path=VOICE_PATH, record={"author": "agent", "text": f"n{i}", "ts": now})
    with pytest.raises(PermissionError) as excinfo:
        tool.run(path=VOICE_PATH, record={"author": "agent", "text": "one too many", "ts": now})
    message = str(excinfo.value)
    assert "voice budget spent" in message
    # Отказ обязан НАЗЫВАТЬ выход, иначе агент угадывает причину и молчит.
    assert "self_improvement_issues" in message


def test_yesterdays_calls_do_not_spend_todays_budget(tmp_path: Path) -> None:
    tool = _voice(tmp_path)
    old = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(days=3)).isoformat()
    for i in range(VOICE_CALLS_PER_DAY * 2):
        tool.run(path=VOICE_PATH, record={"author": "agent", "text": f"old{i}", "ts": old})
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    tool.run(path=VOICE_PATH, record={"author": "agent", "text": "today", "ts": now})


def test_the_operators_own_lines_do_not_spend_the_agents_budget(tmp_path: Path) -> None:
    """Потолок считает обращения АГЕНТА, а не реплики человека."""
    tool = _voice(tmp_path)
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    for i in range(VOICE_CALLS_PER_DAY * 3):
        tool.run(path=VOICE_PATH, record={"author": "operator", "text": f"q{i}", "ts": now})
    tool.run(path=VOICE_PATH, record={"author": "agent", "text": "ответ", "ts": now})


def test_journal_append_is_no_longer_blocked_on_the_autonomous_path() -> None:
    from core.autonomous_runtime import _AUTONOMOUS_GOAL_BLOCKED_TOOLS

    assert "journal_append" not in _AUTONOMOUS_GOAL_BLOCKED_TOOLS

# --------------------------------------------------------------------------
# Потолок на ЖИВОЙ форме записи
# --------------------------------------------------------------------------
def test_the_ceiling_holds_on_the_shape_the_agent_actually_writes(tmp_path: Path) -> None:
    """Запись приходит БЕЗ времени — и потолок обязан работать всё равно.

    Замер 2026-09-23, через час после введения потолка: он НЕ РАБОТАЛ. Живые
    записи в data/chat_outbox.jsonl несут только `author` и `text`; из 12
    записей поле `ts` было у одной — самой первой, писанной руками мимо
    инструмента. Счётчик отбирал записи по дате и потому видел ноль обращений
    за сутки при двух сделанных за этот же день.

    А тесты выше были ЗЕЛЁНЫЕ, потому что ставили `ts` в записи сами. Это
    третий случай за один день, когда зелёный прогон не увидел живой поломки
    (первый — учёт кэша, второй — шапка планировщика). Поэтому здесь запись
    берётся в той форме, в какой она приходит В ЖИЗНИ, и ни одно поле не
    добавляется.
    """
    tool = _voice(tmp_path)
    fired_at = None
    for i in range(1, VOICE_CALLS_PER_DAY + 3):
        try:
            tool.run(path=VOICE_PATH, record={"author": "agent", "text": f"зов {i}"})
        except PermissionError:
            fired_at = fired_at or i
    assert fired_at == VOICE_CALLS_PER_DAY + 1, (
        f"потолок сработал на обращении {fired_at}, а должен на "
        f"{VOICE_CALLS_PER_DAY + 1}: запись без поля `ts` не считалась за сутки"
    )


def test_a_voice_record_is_stamped_at_birth(tmp_path: Path) -> None:
    """Обращение к человеку обязано знать, когда оно сделано.

    Чинится у ИСТОКА, а не в счётчике: считать записи без времени
    «сегодняшними» значило бы мгновенно съесть весь запас старыми записями и
    заткнуть агента, а считать «не сегодняшними» — это и была поломка.
    Без времени нельзя ни отмерить суточный запас, ни прочитать переписку по
    порядку.
    """
    tool = _voice(tmp_path)
    tool.run(path=VOICE_PATH, record={"author": "agent", "text": "без времени"})
    rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "chat_outbox.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    payload = rows[-1].get("payload", rows[-1])
    assert "ts" in payload, "время не проставлено при рождении записи"
    stamp = dt.datetime.fromisoformat(payload["ts"])
    assert stamp.tzinfo is not None, "время без часового пояса — наивная метка"


def test_a_stamp_the_caller_named_is_not_overwritten(tmp_path: Path) -> None:
    """Время, названное вызывающим, остаётся его."""
    tool = _voice(tmp_path)
    mine = "2026-01-02T03:04:05+00:00"
    tool.run(path=VOICE_PATH, record={"author": "agent", "text": "своё время", "ts": mine})
    rows = [
        json.loads(line)
        for line in (tmp_path / "data" / "chat_outbox.jsonl").read_text(
            encoding="utf-8"
        ).splitlines()
        if line.strip()
    ]
    assert rows[-1].get("payload", rows[-1])["ts"] == mine


def test_an_ordinary_journal_is_not_stamped(tmp_path: Path) -> None:
    """Время проставляется только ГОЛОСУ, а не любому журналу.

    Иначе запрет стал бы шире своего основания: у прочих журналов свои
    договоры формы, и дописывать им поля — не дело этой двери.
    """
    from tools.journal_append import _stamp_voice_record

    record = {"author": "agent", "text": "заметка"}
    assert _stamp_voice_record("data/self_improvement_issues.jsonl", record) == record
    assert "ts" not in record
