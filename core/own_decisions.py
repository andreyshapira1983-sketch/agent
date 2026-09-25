"""Чьё решение: о своём коде, проверках и тестах решает агент (обратимо, судят тесты);
человеку — только HUMAN_ONLY. Решения — в data/own_decisions.jsonl, читает core/patch_route.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DECISIONS_RELPATH = "data/own_decisions.jsonl"

#: Когда следующий шаг — не твой. Всё остальное решает агент по уликам.
HUMAN_ONLY: tuple[str, ...] = (
    "деньги",
    "ключи или доступ",
    "выход наружу (отправить, опубликовать, написать кому-то)",
    "необратимое действие",
    "твои улики противоречат друг другу",
)


def rule_text() -> str:
    """Правило для модели — одно и в цели «уткнулся», и в правиле голоса."""
    return ("Решение о твоём коде, проверках и тестах принимаешь ты: такая правка обратима, "
            "её судят patch_check, полный набор тестов и откат, а не человек. Человека спрашивай "
            "только если следующий шаг — " + "; ".join(HUMAN_ONLY) + ".")


def _rows(root: Path) -> list[dict[str, Any]]:
    path = Path(root) / DECISIONS_RELPATH
    out: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8", errors="replace").splitlines()
    except OSError:
        return out
    for line in lines:
        try:
            row = json.loads(line)
        except ValueError:
            continue
        row = row.get("payload", row) if isinstance(row, dict) else row
        if isinstance(row, dict):
            out.append(row)
    return out


def latest_for(root: Path | str, key: str) -> dict[str, Any] | None:
    """Последнее решение, чьё поле `about` называет `key` (отпечаток дефекта или цель)."""
    if not key:
        return None
    for row in reversed(_rows(Path(root))):
        if key in str(row.get("about") or "") and str(row.get("decision") or "").strip():
            return row
    return None


def decision_line(root: Path | str, key: str) -> str:
    """Строка для цели: что агент сам решил по этому предмету, или пусто."""
    row = latest_for(root, key)
    if row is None:
        return ""
    because = str(row.get("because") or "").strip()
    return (f"Твоё решение по этому ({row.get('ts') or 'без даты'}): {str(row['decision']).strip()[:400]}"
            + (f" — потому что {because[:300]}" if because else "") + ". Делай правку по нему. ")
