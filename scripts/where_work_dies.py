"""Где именно дохнет работа: один прогон — три рубежа, отсортированных по цене.

Зачем. Дефекты находились тем, что читающий спотыкался о странность в
журнале: сегодня — снятый шаг, завтра — обвинение детектора. Статистика при
этом каждый раз собиралась в блокноте заново и выбрасывалась, поэтому
следующий заход снова начинался с блуждания, а не с верхней строки списка.
Прибор, который ранжирует, стоит сорок строк и делает удар точечным.

Три рубежа, на которых работа пропадает, — в порядке хода прогона:

1. ШАГ снят правилом допуска и до инструмента не доехал (core/step_sanitizer).
2. ЭПИЗОД записан, но в опыт не допущен (core/smart_memory) — прогон был,
   учиться на нём нельзя.
3. ЦЕЛЬ не признана достигнутой (core/success_check).

Считается по трассам, а не по памяти: `python scripts/where_work_dies.py [дней]`.
"""
from __future__ import annotations

import glob
import json
import os
import re
import sys
import time
from collections import Counter

_STEP = re.compile(r"step\[\d+\]: ([^\"\\]{10,120})")
_QUOTED = re.compile(r"'[^']{0,80}'")
_NUM = re.compile(r"\d+")


def _generalise(reason: str) -> str:
    """Причина без частностей: имя файла и число — не разные причины."""
    return _NUM.sub("N", _QUOTED.sub("'…'", reason))[:90]


def _rows(days: float) -> tuple[Counter, list[dict], Counter]:
    cutoff = time.time() - days * 86400
    steps: Counter = Counter()
    episodes: list[dict] = []
    goals: Counter = Counter()
    for path in glob.glob("logs/trace_*.jsonl"):
        if os.path.getmtime(path) < cutoff:
            continue
        with open(path, encoding="utf-8", errors="replace") as handle:
            for line in handle:
                if "step[" in line:
                    for m in _STEP.finditer(line):
                        steps[_generalise(m.group(1))] += 1
                head = line[:160]
                if ('"episodic_memory_write"' not in head
                        and '"goal_success_check"' not in head):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                payload = record.get("payload") or {}
                if record.get("event") == "episodic_memory_write":
                    episodes.append(payload)
                else:
                    goals[str(payload.get("verdict"))] += 1
    return steps, episodes, goals


def _episode_wall(p: dict) -> str:
    """Первый рубеж, о который эпизод разбился, — или допуск."""
    if p.get("usage_eligible") is True:
        return "допущен в опыт"
    if "self_contradiction" in str(p.get("defect_signals")):
        return "обвинён в самопротиворечии (до всех прочих осей)"
    if p.get("outcome") != "success":
        return f"исход не success: {p.get('outcome')}"
    if str(p.get("completion_state")) != "achieved":
        return f"завершение не achieved: {p.get('completion_state')}"
    if not (p.get("verified_chunks") or 0):
        return "ни одного проверенного утверждения"
    return "не допущен по иной причине"


def main(days: float) -> None:
    steps, episodes, goals = _rows(days)
    print(f"=== за последние {days:g} сут ===\n")
    print(f"ШАГИ, снятые правилом допуска: {sum(steps.values())}")
    for reason, n in steps.most_common(10):
        print(f"  {n:4d}  {reason}")
    walls = Counter(_episode_wall(p) for p in episodes)
    print(f"\nЭПИЗОДЫ: {len(episodes)}")
    for wall, n in walls.most_common(10):
        share = 100.0 * n / max(1, len(episodes))
        print(f"  {n:4d}  ({share:2.0f}%)  {wall}")
    print(f"\nЦЕЛИ, проверенные по следу в мире: {sum(goals.values())}")
    for verdict, n in goals.most_common():
        print(f"  {n:4d}  {verdict}")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 4.0)
