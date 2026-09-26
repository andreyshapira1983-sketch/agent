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
_PATCH_FILE = re.compile(r"proposals/selffix/[\w.\-]+/edits\.txt")
_MEMORY_CITATION = re.compile(r"\s*\[(?:[\w-]+:)*memory:[^\]\s]+\]")


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


def _inside(root: Path, rel: str) -> Path | None:
    target = (root / rel).resolve()
    return target if target.is_relative_to(root.resolve()) else None


def _unlanded(root: Path, rel: str) -> list[str]:
    from tools.patch_check import parse_blocks

    out: list[str] = []
    for block in parse_blocks((root / rel).read_text(encoding="utf-8", errors="replace")):
        target = _inside(root, block["path"])
        text = target.read_text(encoding="utf-8", errors="replace") if target and target.is_file() else ""
        if block["new"].strip() not in text and block["path"] not in out:
            out.append(block["path"])
    return out


def _on_disk(root: Path, rel: str) -> str:
    try:
        target = _inside(root, rel)
        if target is None or not target.is_file():
            return f"{rel} (на диске нет)"
        missing = _unlanded(root, rel) if _PATCH_FILE.fullmatch(rel) else []
    except (OSError, ValueError):
        return f"{rel} (не прочитан)"
    return f"{rel} (правка не поставлена в {', '.join(missing[:3])})" if missing else rel


class PassProgress:
    """Один заход на цель человека: подсказка из прошлых заходов и запись этого."""

    def __init__(self, agent: Any, workspace: Any, goal: str) -> None:
        self.agent, self.goal = agent, goal
        self.root, self.path = Path(workspace), progress_path(workspace, goal)
        self.rel = f"{PROGRESS_DIR}/{self.path.name}"
        self.effects_before = len(_effects(agent))
        agent.memory_given_to_goal = frozenset(
            i for r in _rows(self.path)[-_SHOWN:] for i in (r.get("recalled") or ()))

    def prompt(self, focused_goal: str) -> str:
        rows = _rows(self.path)[-_SHOWN:]
        if not rows:
            return focused_goal
        from core.code_citations import annotate

        lines = [annotate(self.root, f"- {r.get('ts', '')[:16]}: записано: "
                          f"{', '.join(_on_disk(self.root, p) for p in r.get('written') or []) or 'ничего'}; "
                          f"итог (слова захода, не проверка): {r.get('answer') or 'ответа нет'}") for r in rows]
        return (f"{focused_goal}\n\nПрогресс прошлых заходов по этой цели ({self.rel}):\n" + "\n".join(lines)
                + "\nПродолжай с последнего шага: доведи начатое, не перечитывай всё заново; "
                "помеченное «на диске нет» или «правка не поставлена» не сделано, что бы ни говорил итог.")

    def finish(self, answer: str) -> None:
        written: list[str] = []
        for plan in _effects(self.agent)[self.effects_before:]:
            found = _QUOTED_PATH.search(str(getattr(plan, "description", "") or ""))
            if found and found.group(1) not in written:
                written.append(found.group(1))
        text = " ".join(str(answer or "").split())
        text = text[text.find("Conclusion:"):] if "Conclusion:" in text else text  # без шапки «Evidence scope»
        text = _MEMORY_CITATION.sub("", text)
        recalled = [str(getattr(r, "id", "")) for r in getattr(self.agent, "_last_persistent_records", None) or ()]
        self.agent.memory_given_to_goal = frozenset()
        row = {"ts": datetime.now(timezone.utc).isoformat(), "written": written[:10], "answer": text[:_ANSWER_CHARS],
               "recalled": recalled[:10]}
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(row, ensure_ascii=False) + "\n")


def start_pass(agent: Any, workspace: Any, config: Any, action: Any) -> PassProgress | None:
    """Файл ведётся только для захода на цель человека (своя цель агента сменяется драйвами)."""
    if getattr(action, "action", "") != "pursue_goal" or getattr(config, "goal_is_self", False):
        if agent is not None:
            agent.memory_given_to_goal = frozenset()
        return None
    return PassProgress(agent, workspace, str(getattr(config, "goal", "") or ""))
