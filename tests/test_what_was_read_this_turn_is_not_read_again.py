"""Прочитанное в этом ходе не читается заново.

Сквозная проверка 2026-09-21 (вопрос про core/smart_memory.py и
tools/file_write.py): оба файла прочитаны целиком первыми же вызовами, и всё
же два документа из knowledge/doctrine/ открыты по три раза, а в последнем
круге — три окна уже прочитанных целиком файлов. Причины в коде:
`_ensure_memory_governance_docs_first` вставлял документы в КАЖДЫЙ круг, а
пометка «[обрезано: ещё N символов]» в показе планировщику читалась как
«прочитано не всё».
"""
from __future__ import annotations

from types import SimpleNamespace

from core.observation_round import format_observations, reuse_already_read


def _step(label: str, tool: str = "file_read"):
    return SimpleNamespace(action_spec={"tool_name": tool, "source_label": label}, status="pending")


def test_a_repeat_read_and_a_window_of_a_whole_file_are_not_run() -> None:
    artifacts = {
        "file:knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md": {"tool": "file_read", "output": "doc"},
        "file:core/smart_memory.py": {"tool": "file_read", "output": "whole"},
    }
    steps = [
        _step("file:knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md"),
        _step("file:core/smart_memory.py:780-850"),
        _step("file:tools/file_write.py"),
        _step("find_in_files:outcome", tool="find_in_files"),
    ]
    logged: list = []
    attempt: dict = {}
    run = reuse_already_read(
        SimpleNamespace(artifacts=artifacts, plan=SimpleNamespace(steps=steps), attempt=2),
        attempt, lambda e, p: logged.append((e, p)))
    assert [s.action_spec["source_label"] for s in run] == [
        "file:tools/file_write.py", "find_in_files:outcome"]
    assert set(attempt) == {"file:knowledge/doctrine/MEMORY_SYSTEM_AUDIT.md",
                            "file:core/smart_memory.py"}
    assert steps[0].status == "done" and steps[1].status == "done"
    assert logged[0][0] == "already_read_reused"


def test_a_window_does_not_stand_for_the_whole_file() -> None:
    artifacts = {"file:core/smart_memory.py:1-60": {"tool": "file_read", "output": "head"}}
    steps = [_step("file:core/smart_memory.py")]
    run = reuse_already_read(
        SimpleNamespace(artifacts=artifacts, plan=SimpleNamespace(steps=steps), attempt=2),
        {}, lambda e, p: None)
    assert run == steps, "окно не заменяет чтение целиком"


def test_the_preview_says_the_whole_output_is_in_evidence() -> None:
    block = format_observations(SimpleNamespace(steps=[]),
                                {"file:big.py": {"tool": "file_read", "output": "x" * 50_000}})
    assert "preview only" in block
    assert "FULL output is already in your evidence" in block
    assert "обрезано: ещё" not in block


def test_a_skeleton_with_to_be_filled_keeps_the_round() -> None:
    """2026-09-21: приложения записаны заглушками «(заполняется после чтения…)»,
    ход закончился как «чистая запись», и заполнить их было некогда."""
    from core.observation_round import unfilled_placeholders

    assert unfilled_placeholders("# Приложение A\n\n(заполняется после чтения core/approval_inbox.py)")
    assert unfilled_placeholders("(to be filled after the probe)")
    assert not unfilled_placeholders("Функция (строка 12) возвращает список (см. выше).")


def test_a_read_another_step_references_is_run_again() -> None:
    """2026-09-21 ~18:22: второй круг пропустил оба чтения как «уже прочитанные»,
    и запись final.md = {{step:1.output}}… упала с «known steps are []»."""
    artifacts = {"file:proposals/final.md": {"tool": "file_read", "output": "old"}}
    steps = [
        SimpleNamespace(order=1, id="s1", status="pending",
                        action_spec={"tool_name": "file_read", "source_label": "file:proposals/final.md"}),
        SimpleNamespace(order=2, id="s2", status="pending",
                        action_spec={"tool_name": "file_write", "source_label": "file_write:proposals/final.md",
                                     "arguments": {"path": "proposals/final.md",
                                                   "content": "{{step:1.output}} + fix"}}),
    ]
    run = reuse_already_read(
        SimpleNamespace(artifacts=artifacts, plan=SimpleNamespace(steps=steps), attempt=2),
        {}, lambda e, p: None)
    assert [s.order for s in run] == [1, 2], "чтение, нужное ссылке, не пропускается"
