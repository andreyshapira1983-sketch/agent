"""Свои открытые записи о промахах — перед глазами в момент действия.

Замер 2026-09-21. Агент ЗАПИСЫВАЕТ свои промахи точнее, чем их формулирует
оператор. 20.09 в 22:00 он завёл в `data/self_improvement_issues.jsonl`:

    «Explained a wall instead of testing it» — «three refusals narrated
    instead of switching tools. The fix is to attempt the action first and
    report the measured result.»

Через десять часов он трижды запросил строки 361, 481 и 601 файла из 287
строк и объявил чтение невозможным. Рядом лежали его же открытые записи о
сломанной пробе («на этом измерительном приборе стоят все прочие решения») —
и в разговоре он «обнаружил» это заново, как новость.

Реестр читался, но только при ВЫБОРЕ работы (`core/campaign_io.py`). В тот
момент, когда агент ДЕЙСТВУЕТ — планирует шаги или пишет «не могу», — его
собственная запись лежала в ящике. Весь тот день её держали перед ним
оператор и Claude; это и есть механизм, которого не было.

Записи читаются как есть, а не через `SelfImprovementIssue.from_dict`: тот
выбрасывает строки без `fingerprint` (так пропала самая первая запись о
пробе) и теряет поля вне схемы — лекарство из записи выше лежит в
`description`/`proposed_action`, которых схема не знает.

Блок — контекст, не улика и не задание: то, что агент сам о себе записал.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from core.state_integrity import read_state_jsonl

REGISTRY_RELPATH = Path("data") / "self_improvement_issues.jsonl"
_MAX_ENTRIES = 8
_MAX_LINE = 300
_SEVERITY_RANK = {"high": 0, "medium": 1, "low": 2}
#: Где агент пишет лекарство — в порядке, в каком его стоит показать.
_LESSON_FIELDS = (
    "proposed_action", "suggested_next_action", "description",
    "why_it_matters", "symptom", "reason",
)
#: Слова, которые реестр подставляет сам, когда агент лекарства не назвал.
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
    """Открытые записи реестра, как агент их написал: сначала тяжёлые, потом свежие."""
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
    """Блок для планировщика и синтезатора; пустой список — пустая строка."""
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
