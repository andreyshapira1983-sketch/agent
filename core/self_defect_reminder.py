"""Own open defect records, put before the agent's eyes at the moment of acting.

Records are read as written, not through `SelfImprovementIssue.from_dict` (it drops rows without `fingerprint` and fields outside the schema). The block is context, not evidence and not an assignment.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state_integrity import read_state_jsonl

REGISTRY_RELPATH = Path("data") / "self_improvement_issues.jsonl"
_MAX_ENTRIES = 8
_MAX_LINE = 300
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
# Fields where the agent writes the remedy, in the order they are worth showing.
_LESSON_FIELDS = (
    "proposed_action", "suggested_next_action", "description",
    "why_it_matters", "symptom", "reason",
)
# Words the registry substitutes itself when the agent named no remedy.
_BOILERPLATE = frozenset({
    "review the issue evidence and propose one small read-only fix",
})


def _title(row: dict[str, Any]) -> str:
    return " ".join(str(row.get("title") or row.get("issue") or "").split())


def _lesson(row: dict[str, Any]) -> str:
    for key in _LESSON_FIELDS:
        text = " ".join(str(row.get(key) or "").split())
        if text and text.casefold() not in _BOILERPLATE:
            return text
    return ""


def _stamp(row: dict[str, Any]) -> str:
    return str(row.get("last_seen") or row.get("created_at")
               or row.get("opened_at") or row.get("first_seen") or "")


def open_self_defects(workspace: Path | str | None) -> list[dict[str, Any]]:
    """Open registry records as the agent wrote them: severe first, then fresh."""
    if workspace is None:
        return []
    path = Path(workspace) / REGISTRY_RELPATH
    if not path.exists():
        return []
    rows = [
        row for row in read_state_jsonl(path)
        if isinstance(row, dict)
        and str(row.get("status") or "open").casefold() != "resolved"
        and _title(row)
    ]
    rows.sort(key=_stamp, reverse=True)
    rows.sort(key=lambda r: _SEVERITY_RANK.get(str(r.get("severity") or "medium"), 1))
    return rows[:_MAX_ENTRIES]


def format_open_self_defects(rows: list[dict[str, Any]]) -> str:
    """Block for the planner and synthesizer; an empty list gives an empty string."""
    if not rows:
        return ""
    lines = [
        "<open_self_defects>",
        (
            "Твои собственные открытые записи о твоих промахах "
            "(data/self_improvement_issues.jsonl). Это не улика и не задание — "
            "это то, что ты сам о себе записал. Прежде чем написать «не могу», "
            "«недоступно», «данных нет», объявить стену или сдать работу, "
            "проверь, не повторяешь ли ты одну из них. Не цитируй этот блок "
            "как источник."
        ),
    ]
    for row in rows:
        severity = str(row.get("severity") or "medium")
        lesson = _lesson(row)
        line = f"- [{severity}] {_title(row)}" + (f" — {lesson}" if lesson else "")
        lines.append(line if len(line) <= _MAX_LINE else line[: _MAX_LINE - 1] + "…")
    lines.append("</open_self_defects>")
    return "\n".join(lines)
