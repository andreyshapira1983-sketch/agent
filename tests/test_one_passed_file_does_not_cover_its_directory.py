"""Один переданный файл каталога не делает каталог видимым лаборатории.

Живой случай 27.09 (ночь, новый сервер): проба получила в inputs
`logs/daemon_tick.jsonl` и считала `Path("logs").glob("trace_*.jsonl")`. Сторож
счёл каталог `logs` «переданным» по одному файлу и промолчал; проба напечатала
«trace_files 0», и агент ответил «субагенты не запускались» — в trace их 19.
"""
from __future__ import annotations

from pathlib import Path

from tools.python_probe import PythonProbeTool

_CODE = (
    'from pathlib import Path\n'
    'print("trace_files", len(list(Path("logs").glob("trace_*.jsonl"))))\n'
    'print(open("logs/daemon_tick.jsonl").read().strip())\n'
)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    (tmp_path / "logs" / "daemon_tick.jsonl").write_text('{"tick": 1}\n', encoding="utf-8")
    (tmp_path / "logs" / "trace_a.jsonl").write_text('{"event": "subagent_start"}\n', encoding="utf-8")
    return tmp_path


def test_a_glob_beside_one_passed_file_is_still_called_out(tmp_path: Path) -> None:
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    result = probe.run(code=_CODE, inputs=["logs/daemon_tick.jsonl"])

    assert "trace_files 0" in result["stdout"], "лаборатория вдруг увидела журналы"
    assert "logs/trace_*.jsonl" in result["missing_inputs"], result["missing_inputs"]
    assert "not a measurement" in (result.get("note") or ""), result.get("note")


def test_passing_every_file_of_the_glob_removes_the_warning(tmp_path: Path) -> None:
    probe = PythonProbeTool(workspace_root=_workspace(tmp_path))

    result = probe.run(code=_CODE, inputs=["logs/daemon_tick.jsonl", "logs/trace_a.jsonl"])

    assert "trace_files 1" in result["stdout"], result
    assert not result["missing_inputs"], result["missing_inputs"]
