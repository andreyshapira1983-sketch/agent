"""Цель по драйву выдаётся только проверенной; сбой модели и слабые драйвы дают отказ, а не аварию."""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

import core.drive_goal as dg

_NOW = datetime(2026, 9, 26, 12, 0, tzinfo=timezone.utc)
_GOOD = json.dumps({"goal": "Посчитай сумму первых ста натуральных чисел и проверь её в python_probe",
                    "success_check": "В ответе названо число 5050"}, ensure_ascii=False)
_MISSING_FILE = json.dumps({"goal": "Найди в книге определение группы и процитируй его",
                            "success_check": "Цитата из math_study/no_such_book.txt"}, ensure_ascii=False)


class _LLM:
    def __init__(self, replies: list[str] | None = None, error: Exception | None = None) -> None:
        self.replies = list(replies or [])
        self.error = error
        self.users: list[str] = []

    def complete(self, *, system: str, user: str, max_tokens: int, temperature: float) -> str:
        self.users.append(user)
        if self.error is not None:
            raise self.error
        return self.replies.pop(0)


def _decisions(root: Path) -> list[dict]:
    path = root / dg.DECISIONS_RELPATH
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def test_a_model_failure_is_a_declined_choice_not_a_crash(tmp_path: Path) -> None:
    """Упавшая модель даёт отказ выбора, и отказ записан в журнал решений."""
    report = dg.propose_drive_goal(_LLM(error=RuntimeError("provider down")), tmp_path, now=_NOW)

    assert report.status == "declined"
    assert "вызов модели не удался" in report.reason
    assert [d["status"] for d in _decisions(tmp_path)] == ["declined"]


def test_a_goal_naming_a_missing_file_is_sent_back_then_declined(tmp_path: Path) -> None:
    """Цель с несуществующим файлом в критерии возвращается модели с причиной и в итоге не выдаётся."""
    llm = _LLM(replies=[_MISSING_FILE] * 3)

    report = dg.propose_drive_goal(llm, tmp_path, now=_NOW, attempts=3)

    assert report.status == "declined"
    assert "no_such_book.txt" in report.reason
    assert len(llm.users) == 3
    assert "Прошлое предложение отклонено" not in llm.users[0]
    assert all("no_such_book.txt" in user for user in llm.users[1:])
    assert [d["status"] for d in _decisions(tmp_path)] == ["declined"]


def test_a_checked_goal_is_given_and_recorded(tmp_path: Path) -> None:
    """Контроль: цель, прошедшая проверку, выдаётся с критерием и записывается."""
    report = dg.propose_drive_goal(_LLM(replies=[_GOOD]), tmp_path, now=_NOW)

    assert report.status == "proposed", report
    assert report.goal.startswith("Посчитай сумму первых ста натуральных чисел")
    assert report.drive.startswith("competence_")
    assert f"{dg.NOTES_DIR}/" in report.success_check, "критерий задаёт код: конспект, а не слова модели"
    rows = _decisions(tmp_path)
    assert [d["status"] for d in rows] == ["proposed"]
    assert rows[0]["goal"] == report.goal


def test_no_drive_above_the_content_floor_means_no_model_call(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Все драйвы ниже порога — отказ без вызова модели."""
    real = dg.compute_drives

    def _quiet(root: Path, now: datetime) -> dict:
        return {name: {**info, "value": 0.0} for name, info in real(root, now).items()}

    monkeypatch.setattr(dg, "compute_drives", _quiet)
    llm = _LLM(error=AssertionError("модель не должна вызываться"))

    report = dg.propose_drive_goal(llm, tmp_path, now=_NOW)

    assert report.status == "declined"
    assert llm.users == []
    assert [d["status"] for d in _decisions(tmp_path)] == ["declined"]
