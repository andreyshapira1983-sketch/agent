"""Файл прогресса цели человека: заход читает его первым и дописывает в конце.

Заходы кампании — смены без памяти: 26.09 три часа заходов на «подключи модели» перечитывали
одни и те же образцы, а начатая правка proposals/selffix/local_models_tools/edits.txt стояла;
в разговоре, где ему назвали сделанное и следующий шаг, она переписалась за 2 минуты.
Приём — файл прогресса между сессиями (Anthropic, «Effective harnesses for long-running agents»).
"""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

PROGRESS_DIR = "data/goal_progress"
_SHOWN = 4
_ANSWER_CHARS = 900
_QUOTED_PATH = re.compile(r"'([^']+)'")


def progress_path(workspace: Any, goal: str) -> Path:
    key = hashlib.sha256(" ".join(goal.split()).encode("utf-8")).hexdigest()[:12]
    return Path(workspace) / PROGRESS_DIR / f"{key}.jsonl"


def _rows(path: Path) -> list[dict[str, Any]]:
    try:
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except (OSError, ValueError):
        return []


def _effects(agent: Any) -> list[Any]:
    return list(getattr(agent, "compensation_log", None) or ())


class PassProgress:
    """Один заход на цель человека: подсказка из прошлых заходов и запись этого."""

    def __init__(self, agent: Any, workspace: Any, goal: str) -> None:
        self.agent, self.goal = agent, goal
        self.path = progress_path(workspace, goal)
        self.rel = f"{PROGRESS_DIR}/{self.path.name}"
        self.effects_before = len(_effects(agent))

    def prompt(self, focused_goal: str) -> str:
        rows = _rows(self.path)[-_SHOWN:]
        if not rows:
            return focused_goal
        lines = [f"- {r.get('ts', '')[:16]}: записано: {', '.join(r.get('written') or []) or 'ничего'}; "
                 f"итог: {r.get('answer') or 'ответа нет'}" for r in rows]
        return (f"{focused_goal}\n\nПрогресс прошлых заходов по этой цели ({self.rel}):\n" + "\n".join(lines)
                + "\nПродолжай с последнего шага: доведи начатое, не перечитывай всё заново.")

    def finish(self, answer: str) -> None:
        written: list[str] = []
        for plan in _effects(self.agent)[self.effects_before:]:
            found = _QUOTED_PATH.search(str(getattr(plan, "description", "") or ""))
            if found and found.group(1) not in written:
                written.append(found.group(1))
        text = " ".join(str(answer or "").split())
        text = text[text.find("Conclusion:"):] if "Conclusion:" in text else text  # без шапки «Evidence scope»
        row = {"ts": datetime.now(timezone.utc).isoformat(), "written": written[:10], "answer": text[:_ANSWER_CHARS]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def start_pass(agent: Any, workspace: Any, config: Any, action: Any) -> PassProgress | None:
    """Файл ведётся только для захода на цель человека (своя цель агента сменяется драйвами)."""
    if getattr(action, "action", "") != "pursue_goal" or getattr(config, "goal_is_self", False):
        return None
    return PassProgress(agent, workspace, str(getattr(config, "goal", "") or ""))
