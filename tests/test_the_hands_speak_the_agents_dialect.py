"""Руки понимают диалект своего хозяина — три починки одного дня (2026-08-29).

Слово оператора: «начни лечить болезни, при которых он не может кодировать».
Все три найдены живой перепиской-допросом 2026-08-28: агент рассуждал верно,
а падал о собственное тело.

1. Модель зовёт findstr с POSIX-путём («logs/daemon_tick.jsonl») — findstr
   режет прямую косую, ищет файл не там и падает. Нормализатор путей
   существовал и знал этот класс дословно, но включался только при подмене
   grep→findstr: предпосылка «запрошенный бинарь понимает свой диалект»
   умерла — бинарь понимает, а ЗОВУЩИЙ говорит на POSIX.
2. Причина отказа приходила кракозябрами: консоль Windows отвечает в OEM
   (cp866), а декодер знал только UTF-8 — «не удалось открыть» превращалось
   в «�� �������», и агент не мог прочитать, почему ему отказали.
3. Читатель трасс требовал точное имя «trace_<hex>»: голый hex собственной
   трассы получал «файла нет» и ноль событий без подсказки.

Красные свидетели: до починки каждый первый тест группы падал ровно живой
формой отказа из переписки.
"""
from __future__ import annotations

import json

from tools.read_logs import ReadLogsTool
from tools.shell_exec import ShellExecTool

# ── 1. findstr и прямые косые ────────────────────────────────────────────────

def test_a_posix_path_is_translated_for_findstr_even_without_substitution(
    monkeypatch,
) -> None:
    import tools.shell_exec as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    out = ShellExecTool._normalise_argv_for(
        ["findstr", "2026-08-28T08:31", "logs/daemon_tick.jsonl"],
        substituted=False,
        real_cmd="findstr",
    )

    assert out[-1] == "logs\\daemon_tick.jsonl", (
        "прямой вызов findstr остался без перевода путей — "
        "живой отказ 2026-08-28 вернулся")


def test_switches_and_patterns_stay_untouched(monkeypatch) -> None:
    """Граница: ключи (/S, /C:...) и шаблоны без косых не трогаются."""
    import tools.shell_exec as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    out = ShellExecTool._normalise_argv_for(
        ["findstr", "/S", "/N", "/C:core/x", "pattern", "*.py"],
        substituted=False,
        real_cmd="findstr",
    )

    assert out == ["findstr", "/S", "/N", "/C:core/x", "pattern", "*.py"]


def test_other_binaries_keep_their_own_dialect(monkeypatch) -> None:
    """Граница: git и прочие понимают прямые косые — их не переводим."""
    import tools.shell_exec as mod

    monkeypatch.setattr(mod.sys, "platform", "win32")
    out = ShellExecTool._normalise_argv_for(
        ["git", "log", "--", "core/loop.py"],
        substituted=False,
        real_cmd="git",
    )

    assert out[-1] == "core/loop.py"


# ── 2. OEM-кодировка отказов ─────────────────────────────────────────────────

def test_an_oem_refusal_is_readable_not_mojibake(monkeypatch, tmp_path) -> None:
    import tools.shell_exec as mod

    monkeypatch.setattr(mod, "_oem_encoding", lambda: "cp866")
    tool = ShellExecTool(workspace_root=tmp_path)
    raw = "FINDSTR: Не удалось открыть daemon_tick.jsonl".encode("cp866")

    text, truncated = tool._cap_and_decode(raw)

    assert "Не удалось открыть" in text, (
        "причина отказа снова пришла кракозябрами — агенту нечем её прочитать")
    assert truncated is False


def test_utf8_still_wins_when_it_decodes(monkeypatch, tmp_path) -> None:
    import tools.shell_exec as mod

    monkeypatch.setattr(mod, "_oem_encoding", lambda: "cp866")
    tool = ShellExecTool(workspace_root=tmp_path)

    text, _ = tool._cap_and_decode("обычный utf-8 вывод".encode())

    assert text == "обычный utf-8 вывод"


def test_undecodable_bytes_still_degrade_not_crash(monkeypatch, tmp_path) -> None:
    import tools.shell_exec as mod

    monkeypatch.setattr(mod, "_oem_encoding", lambda: None)
    tool = ShellExecTool(workspace_root=tmp_path)

    text, _ = tool._cap_and_decode(b"\xff\xfe\x81")

    assert isinstance(text, str) and text  # replace, не исключение


# ── 3. Читатель трасс и голый идентификатор ─────────────────────────────────

def _logs_with_trace(tmp_path, stem: str) -> ReadLogsTool:
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / f"{stem}.jsonl").write_text(
        json.dumps({"event": "campaign_complete", "payload": {}}) + "\n",
        encoding="utf-8",
    )
    return ReadLogsTool(workspace_root=tmp_path)


def test_a_bare_hex_id_finds_its_own_trace(tmp_path) -> None:
    tool = _logs_with_trace(tmp_path, "trace_4f40f42689b6ea86")

    out = tool.run(trace_id="4f40f42689b6ea86")

    assert out["events_returned"] == 1, (
        "голый идентификатор снова получил «файла нет» — "
        "живой отказ раунда 7 вернулся")
    assert out["trace_id"] == "trace_4f40f42689b6ea86"


def test_an_exact_stem_is_preferred_over_prefixing(tmp_path) -> None:
    """Граница: точное имя остаётся первым — префикс лишь запасной ход."""
    tool = _logs_with_trace(tmp_path, "trace_abc")

    assert tool.run(trace_id="trace_abc")["events_returned"] == 1


def test_traversal_is_still_refused(tmp_path) -> None:
    import pytest

    tool = _logs_with_trace(tmp_path, "trace_abc")

    with pytest.raises(PermissionError):
        tool.run(trace_id="..\\secrets")
