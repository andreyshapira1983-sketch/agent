"""Матрица решений: один фактор меняется, остальные держатся.

Замер, отвергнутые варианты и границы: MIR-160 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.best_next_action import (
    resolve_goal_subject,
    select_best_next_action,
    unresolved_goal_targets,
)

#: Живой репозиторий как источник истины о существовании файлов: предмет цели
#: разрешается ровно так же, как в бою.
_ROOT = Path(__file__).resolve().parents[1]


def _exists(rel: str) -> bool:
    return (_ROOT / rel).is_file()


#: Базовые сигналы — «всё здорово». Каждый сценарий ниже меняет РОВНО ОДНО.
_BASE: dict[str, object] = {
    "result_status": "ok",
    "tests_health": "ok",
    "dry_run_streak": 0,
    "heartbeat_missing": False,
    "heartbeat_stale": False,
    "inbox_pending": 0,
    "self_improvement_registry_available": True,
    "open_self_improvement_issues": (),
}

_ISSUE_LOOP = {
    "title": "loop swallows a refusal",
    "status": "open",
    "occurrences": 3,
    "related_files": ["core/loop.py"],
    "evidence": ["detector fired three times"],
}
_ISSUE_CLOSED = {**_ISSUE_LOOP, "status": "resolved"}
_ISSUE_NO_FILE = {
    "title": "something repeats and nobody knows why",
    "status": "open",
    "occurrences": 2,
    "evidence": ["detector fired twice"],
}

_GOAL_LOOP = "Проследить core/loop.py и описать его места вызова"
_GOAL_OTHER = "Проследить core/subagent_registry и описать его места вызова"
_GOAL_NONE = "Разобраться, почему агент повторяет одно и то же"
_GOAL_GHOST = "Проследить core/no_such_module и описать его места вызова"
_GOAL_SYNTH = "Проследить core/loop_synthesis.py и описать его места вызова"

#: Дефект, названный ВТОРЫМ: до починки его не видели никогда, потому что
#: генератор возвращал первую запись по порядку файла.
_ISSUE_SYNTH = {
    "title": "Investigate recurring detector signal: self_contradiction",
    "status": "open",
    "occurrences": 4,
    "related_files": ["core/loop_synthesis.py"],
    "evidence": ["detector fired four times"],
    "action": "investigate_detector_signal",
}

#: (имя, что меняется, переопределения). Порядок — от «нечего делать» к помехам.
SCENARIOS: list[tuple[str, str, dict]] = [
    ("пустой бэклог, всё здорово", "ничего", {"goal": _GOAL_LOOP}),
    ("дефект про ТОТ ЖЕ файл", "цель совпала с предметом дефекта",
     {"goal": _GOAL_LOOP, "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("дефект про ДРУГОЙ файл", "только цель",
     {"goal": _GOAL_OTHER, "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("цель без предмета", "только цель",
     {"goal": _GOAL_NONE, "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("цель про несуществующий файл", "только цель",
     {"goal": _GOAL_GHOST, "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("работа уже закрыта", "статус записи дефекта",
     {"goal": _GOAL_LOOP, "open_self_improvement_issues": (_ISSUE_CLOSED,)}),
    ("дефект без названного файла", "у записи нет related_files",
     {"goal": _GOAL_LOOP, "open_self_improvement_issues": (_ISSUE_NO_FILE,)}),
    ("два дефекта, один про предмет цели", "порядок в бэклоге",
     {"goal": _GOAL_SYNTH,
      "open_self_improvement_issues": (_ISSUE_NO_FILE, _ISSUE_SYNTH)}),
    ("те же два, цель про третий файл", "только цель",
     {"goal": _GOAL_OTHER,
      "open_self_improvement_issues": (_ISSUE_NO_FILE, _ISSUE_SYNTH)}),
    ("сломанные тесты при чужой цели", "сигнал поломки",
     {"goal": _GOAL_OTHER, "result_status": "failed", "tests_health": "fail",
      "failed_tests": ("tests/test_x.py::test_y",),
      "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("демон молчит при чужой цели", "сигнал поломки",
     {"goal": _GOAL_OTHER, "heartbeat_missing": True,
      "open_self_improvement_issues": (_ISSUE_LOOP,)}),
    ("ошибка тика при чужой цели", "сигнал поломки",
     {"goal": _GOAL_OTHER, "tick_error": "ZeroDivisionError",
      "open_self_improvement_issues": (_ISSUE_LOOP,)}),
]


def run() -> list[dict[str, object]]:
    rows: list[dict[str, object]] = []
    for name, varied, overrides in SCENARIOS:
        goal = str(overrides.get("goal") or "")
        subject = resolve_goal_subject(goal, exists=_exists)
        kwargs = {
            **_BASE, **overrides,
            "goal_subject": subject,
            "goal_names_missing": unresolved_goal_targets(goal, exists=_exists),
        }
        picked = select_best_next_action(**kwargs)  # type: ignore[arg-type]
        rows.append({
            "scenario": name,
            "varied": varied,
            "subject": subject,
            "action": picked.action,
            "severity": picked.severity,
            "grounds": picked.grounds,
            "decided_by": picked.decided_by,
            "considered": picked.candidates_considered,
            "target_path": picked.target_path,
        })
    return rows


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--json", action="store_true")
    args = ap.parse_args()
    rows = run()
    if args.json:
        print(json.dumps(rows, ensure_ascii=False, indent=2))
        return 0
    print(f"{'сценарий':34} {'предмет':30} {'действие':34} {'основание':16} {'как решено':16} канд.")
    for r in rows:
        print(
            f"{str(r['scenario'])[:33]:34} {str(r['subject'] or '—')[:29]:30} "
            f"{str(r['action'])[:33]:34} {str(r['grounds'])[:15]:16} "
            f"{str(r['decided_by'])[:15]:16} {r['considered']}"
        )
    return 0


if __name__ == "__main__":
    sys.exit(main())
