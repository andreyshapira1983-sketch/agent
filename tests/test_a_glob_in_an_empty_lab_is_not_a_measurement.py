"""Ноль по шаблону пути, которого лаборатория не видит, — не замер.

Замер 2026-09-24, ночь кампании: девять проб подряд делали
glob("logs/trace_*.jsonl") без inputs, в пустой временной папке лаборатории
получали ноль, и агент докладывал «поле refutations пустое» — при 93
записях в журналах. Строка со звёздочкой не файл и не каталог, и сторож
`_missing_inputs` её пропускал. Тот же невидимый отказ, что закрыт для
каталогов в test_a_count_over_an_unseen_directory_is_not_a_measurement.
"""
from __future__ import annotations

from pathlib import Path

from tools.python_probe import PythonProbeTool

_CODE = (
    "import glob\n"
    'files = glob.glob("logs/trace_*.jsonl")\n'
    'print("traces", len(files))\n'
)


def _workspace(tmp_path: Path) -> Path:
    (tmp_path / "logs").mkdir()
    for name in ("trace_a.jsonl", "trace_b.jsonl"):
        (tmp_path / "logs" / name).write_text("{}\n", encoding="utf-8")
    return tmp_path


def test_a_glob_over_unseen_files_is_called_out(tmp_path: Path) -> None:
    """Ломалось здесь: missing_inputs пуст, пояснения нет, ноль выглядит фактом."""
    result = PythonProbeTool(workspace_root=_workspace(tmp_path)).run(code=_CODE)
    assert "traces 0" in result["stdout"], "проба вдруг увидела рабочую папку"
    assert result["missing_inputs"] == ["logs/trace_*.jsonl"], result["missing_inputs"]
    assert "GLOB PATTERNS" in (result.get("note") or "")


def test_a_glob_matching_nothing_stays_quiet(tmp_path: Path) -> None:
    """Ломка наоборот: шаблон, под который в папке ничего нет, — не тревога."""
    result = PythonProbeTool(workspace_root=tmp_path).run(code=_CODE)
    assert result["missing_inputs"] == []


def test_a_glob_whose_files_were_passed_stays_quiet(tmp_path: Path) -> None:
    ws = _workspace(tmp_path)
    result = PythonProbeTool(workspace_root=ws).run(
        code=_CODE, inputs=["logs/trace_a.jsonl", "logs/trace_b.jsonl"])
    assert result["missing_inputs"] == []
    assert "traces 2" in result["stdout"]


def test_an_enveloped_journal_is_explained(tmp_path: Path) -> None:
    """Замер 2026-09-24: поля читались снаружи конверта, и «пусто» ушло как факт."""
    (tmp_path / "data").mkdir()
    (tmp_path / "data" / "episodic_memory.jsonl").write_text(
        '{"_integrity": {"alg": "sha256"}, "payload": {"trace_id": "trace_1"}}\n', encoding="utf-8")
    result = PythonProbeTool(workspace_root=tmp_path).run(
        code='import json\nrow = json.loads(open("data/episodic_memory.jsonl").readline())\n'
             'print("trace_id:", row.get("trace_id"))\n',
        inputs=["data/episodic_memory.jsonl"])
    assert "trace_id: None" in result["stdout"]
    assert "payload" in (result.get("note") or ""), result.get("note")
