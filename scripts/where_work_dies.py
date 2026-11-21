"""Rank where work is lost in recent traces: rejected steps, unadmitted episodes, unmet goals."""
from __future__ import annotations

import glob
import json
import os
import re
import subprocess
import sys
import time
from collections import Counter

_STEP = re.compile(r"step\[\d+\]: ([^\"\\]{10,120})")
_QUOTED = re.compile(r"'[^']{0,80}'")
_NUM = re.compile(r"\d+")


def _generalise(reason: str) -> str:
    """Strip specifics from a reason: another file name or number is not another reason."""
    return _NUM.sub("N", _QUOTED.sub("'…'", reason))[:90]


def _rows(days: float) -> tuple[Counter, list[dict], Counter, Counter]:
    cutoff = time.time() - days * 86400
    steps: Counter = Counter()
    episodes: list[dict] = []
    goals: Counter = Counter()
    refutations: Counter = Counter()
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
                        and '"goal_success_check"' not in head
                        and '"verification"' not in head):
                    continue
                try:
                    record = json.loads(line)
                except ValueError:
                    continue
                payload = record.get("payload") or {}
                event = record.get("event")
                if event == "episodic_memory_write":
                    episodes.append(payload)
                elif event == "goal_success_check":
                    goals[str(payload.get("verdict"))] += 1
                elif event == "verification":
                    for item in payload.get("refutations") or ():
                        refutations[str(item.get("code") or "?")] += 1
    return steps, episodes, goals, refutations


def _episode_wall(p: dict) -> str:
    """Return the first gate that kept the episode out of experience, or its admission."""
    if p.get("usage_eligible") is True:
        return "допущен в опыт"
    # The signal list comes from the rule itself so this report cannot drift from it.
    from core.smart_memory import DISQUALIFYING_DEFECT_SIGNALS

    hit = sorted(DISQUALIFYING_DEFECT_SIGNALS & set(p.get("defect_signals") or ()))
    if hit:
        return f"сигнал {', '.join(hit)} (до всех прочих осей)"
    if p.get("outcome") != "success":
        return f"исход не success: {p.get('outcome')}"
    if str(p.get("completion_state")) != "achieved":
        return f"завершение не achieved: {p.get('completion_state')}"
    if not (p.get("verified_chunks") or 0):
        return "ни одного проверенного утверждения"
    return "не допущен по иной причине"


def _nerve_map_verdict() -> list[str]:
    """Names of failing CNS census tests: names, unlike a count, expose a swapped failure."""
    try:
        done = subprocess.run(
            [sys.executable, "-m", "pytest", "-q", "-p", "no:randomly",
             "-k", "cns", "--no-header", "-rf"],
            capture_output=True, text=True, timeout=300, check=False,
        )
    except (OSError, subprocess.SubprocessError) as err:
        return [f"проверить не удалось: {type(err).__name__}"]
    return [ln.split("::", 1)[-1].split(" ")[0]
            for ln in done.stdout.splitlines() if ln.startswith("FAILED")]


def main(days: float) -> None:
    """Print the report for the last ``days`` days; run: where_work_dies.py [days], default 4."""
    steps, episodes, goals, refutations = _rows(days)
    print(f"=== за последние {days:g} сут ===\n")
    print(f"ШАГИ, снятые правилом допуска: {sum(steps.values())}")
    for reason, n in steps.most_common(10):
        print(f"  {n:4d}  {reason}")
    walls = Counter(_episode_wall(p) for p in episodes)
    print(f"\nЭПИЗОДЫ: {len(episodes)}")
    for wall, n in walls.most_common(10):
        share = 100.0 * n / max(1, len(episodes))
        print(f"  {n:4d}  ({share:2.0f}%)  {wall}")
    # Refutation reason codes; traces older than the logging change have none.
    print(f"\nОПРОВЕРЖЕНИЯ по коду причины: {sum(refutations.values())}")
    for code, n in refutations.most_common(10):
        print(f"  {n:4d}  {code}")
    if not refutations:
        print("  (пусто: прогон старше правки, писавшей причину в журнал)")

    print(f"\nЦЕЛИ, проверенные по следу в мире: {sum(goals.values())}")
    for verdict, n in goals.most_common():
        print(f"  {n:4d}  {verdict}")

    broken = _nerve_map_verdict()
    print("\nКАРТА НЕРВНОЙ СИСТЕМЫ:",
          "согласна с телом" if not broken else "РАСХОДИТСЯ С ТЕЛОМ")
    for name in broken:
        print(f"  ✗ {name}")


if __name__ == "__main__":
    main(float(sys.argv[1]) if len(sys.argv) > 1 else 4.0)
