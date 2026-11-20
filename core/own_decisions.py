"""Whose decision it is: the agent decides about its own code, checks and tests (reversible, tests judge); a human only for HUMAN_ONLY. Decisions go to data/own_decisions.jsonl, read by core/patch_route.py."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

DECISIONS_RELPATH = "data/own_decisions.jsonl"

#: When the next step is not the agent's; everything else it decides from evidence.
HUMAN_ONLY: tuple[str, ...] = (
    "деньги",
    "ключи или доступ",
    "выход наружу (отправить, опубликовать, написать кому-то)",
    "необратимое действие",
    "твои улики противоречат друг другу",
)


def rule_text() -> str:
    """Rule for the model: one rule both for a stuck goal and for the voice rule."""
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
    """Latest decision whose `about` field names `key` (defect fingerprint or goal)."""
    if not key:
        return None
    for row in reversed(_rows(Path(root))):
        if key in str(row.get("about") or "") and str(row.get("decision") or "").strip():
            return row
    return None


def decision_line(root: Path | str, key: str) -> str:
    """Goal line: what the agent itself decided about this subject, or empty."""
    row = latest_for(root, key)
    if row is None:
        return ""
    because = str(row.get("because") or "").strip()
    return (f"Твоё решение по этому ({row.get('ts') or 'без даты'}): {str(row['decision']).strip()[:400]}"
            + (f" — потому что {because[:300]}" if because else "") + ". Делай правку по нему. ")
