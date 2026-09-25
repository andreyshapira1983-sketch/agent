"""Предсказание помощника против факта — сверка при его завершении.

Журнал оператора, «Память и под-агенты» (25.09): модель легко пишет
убедительное обоснование для чего угодно, поэтому текст «почему» — слабая
защита. Сильным его делает проверяемое предсказание, которое при удалении
под-агента сравнивается с фактом; систематическое завышение ожиданий тогда
видно в журнале.

Что проверяемо. У помощника жёсткий потолок в три вызова (planner_prompt),
так что предсказывать вызовы незачем. Проверяемо — НА СКОЛЬКИХ ВНЕШНИХ
ИСТОЧНИКАХ будет стоять ответ: образец поручения в подсказке планировщику —
«3 papers with URLs and years». Факт — `external_evidence_count` результата.
Anthropic («How we built our multi-agent research system», 2025): каждому
под-агенту — цель, формат результата и границы; бюджет называется в поручении.

Предсказание берётся из явного `expect_sources`, иначе — первое число в тексте
`expect` («3–5 принципов» → 3), иначе оно «непроверяемо» и так и считается:
доля непроверяемых — тоже цифра, а не молчание.
"""
from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

RELPATH = Path("data") / "subagent_predictions.jsonl"
_FIRST_NUMBER = re.compile(r"(?<![\w.])(\d{1,2})(?![\w.])")


def predicted_sources(expect: str, stated: Any = None) -> tuple[int | None, str]:
    """(число источников, откуда оно: stated | parsed | unchecked)."""
    if isinstance(stated, int) and not isinstance(stated, bool) and 0 <= stated <= 20:
        return stated, "stated"
    m = _FIRST_NUMBER.search(expect or "")
    return (int(m.group(1)), "parsed") if m else (None, "unchecked")


def child_tool_calls(log_dir: Path | str, trace_id: str) -> int | None:
    """Сколько вызовов инструментов сделал помощник — по его собственному следу."""
    try:
        text = (Path(log_dir) / f"{trace_id}.jsonl").read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    return sum(1 for line in text.splitlines() if '"event": "tool_call"' in line)


def compare(expected: int | None, sources: int, status: str) -> bool | None:
    """Сбылось ли: помощник кончил успехом и вернул не меньше обещанных источников."""
    if expected is None:
        return None
    return status == "success" and sources >= expected


def record(workspace: Path | str, row: dict[str, Any]) -> None:
    path = Path(workspace) / RELPATH
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"ts": datetime.now(timezone.utc).isoformat(), **row}, ensure_ascii=False) + "\n")
    except OSError:
        return


def tally(workspace: Path | str) -> dict[str, Any]:
    """Сводка по журналу: сколько предсказаний проверяемо и сколько сбылось."""
    try:
        lines = (Path(workspace) / RELPATH).read_text(encoding="utf-8").splitlines()
    except OSError:
        lines = []
    rows = []
    for line in lines:
        try:
            rows.append(json.loads(line))
        except ValueError:
            continue
    checked = [r for r in rows if r.get("met") is not None]
    missed = [r for r in checked if not r["met"]]
    return {
        "runs": len(rows),
        "checkable": len(checked),
        "met": len(checked) - len(missed),
        "met_share": round((len(checked) - len(missed)) / len(checked), 3) if checked else None,
        "promised_sources": sum(int(r.get("expected_sources") or 0) for r in checked),
        "delivered_sources": sum(int(r.get("sources") or 0) for r in checked),
        "missed_by_role": dict(Counter(str(r.get("role") or "") for r in missed).most_common(5)),
        "usd": round(sum(float(r.get("usd") or 0) for r in rows), 4),
    }
