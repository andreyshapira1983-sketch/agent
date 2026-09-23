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
