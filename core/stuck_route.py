"""Уткнулся — спроси: сначала интернет, потом партнёра.

Слово оператора 2026-09-22 вечером: «есть ещё корень, который ты не заметил:
он забывает, что у него есть интернет и партнёр». Замер того дня: агент сам
заговорил первым 3 раза за всё время, а в интернет ходил почти только там,
где цель прямо этого требовала. Голос описан в подсказке планировщика как
совет — а совет не механизм: пока за «спросить» не отвечает потребность,
он молчит и крутит своё.

Здесь застревание становится величиной: одинаковые пустые циклы и дефекты,
за которые он брался дважды без зелёной правки, поднимают драйв `stuck_need`.
Его цель — детерминированная: найти первоисточник в интернете, а если он не
решает, написать вопрос человеку в data/chat_outbox.jsonl (единственный
способ заговорить первым). Критерий ставит код, проверяет машина: свежая
запись в голосовом ящике.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTBOX_RELPATH = "data/chat_outbox.jsonl"
#: Начало цели «спроси». Её собственный провал считается, но не цитируется:
#: 24.09 каждый провал вкладывал её текст в следующую — «Ты застрял: цель «Ты
#: застрял: цель «Почини…»»», а предмет уходил за обрезку в 120 символов.
STUCK_PREFIX = "Ты застрял:"
_UNPRODUCTIVE = frozenset({"empty", "failed", "idle", "blocked"})
MAX_EVIDENCE = 3


def _rows(path: Path) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    try:
        for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
            if line.strip():
                row = json.loads(line)
                out.append(row.get("payload", row))
    except (OSError, ValueError):
        return out
    return out


def stuck_evidence(root: Path | str) -> list[str]:
    """Чем подтверждается застревание: повторы впустую и брошенные дефекты."""
    root = Path(root)
    evidence: list[str] = []
    ledger = _rows(root / "data" / "campaign_ledger.jsonl")[-12:]
    goals: dict[str, int] = {}
    asks_failed = 0
    for row in ledger:
        if str(row.get("result")) in _UNPRODUCTIVE:
            goal = str(row.get("goal") or "")
            if goal.startswith(STUCK_PREFIX):
                asks_failed += 1
                continue
            goals[goal[:120]] = goals.get(goal[:120], 0) + 1
    evidence += [f"цель «{goal}» впустую {count} раз подряд"
                 for goal, count in goals.items() if goal and count >= 2]
    if asks_failed >= 2:
        evidence.append(f"цель «спроси» (интернет, потом человек) сама прошла впустую {asks_failed} раз")
    try:
        state = json.loads((root / "data" / "patch_route_state.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        state = {}
    applied = sum(state.get("applied", {}).values())
    attempted = len(state.get("attempted", {}))
    if attempted >= 2 and applied == 0:
        evidence.append(f"дефектов взято в починку: {attempted}, поставлено правок: 0")
    return evidence[:MAX_EVIDENCE]


def stuck_goal(root: Path | str) -> Any:
    """Цель «спроси»: первоисточник в интернете, иначе вопрос человеку."""
    from core.drive_goal import DriveGoal

    evidence = stuck_evidence(root)
    if not evidence:
        return None
    facts = "; ".join(evidence)
    goal = (f"Ты застрял: {facts}. Сначала поищи в интернете первоисточник по тому, что не выходит "
            "(web_search и web_fetch), и назови найденное. Если первоисточник не решает — напиши "
            "человеку сам: journal_append path='data/chat_outbox.jsonl', "
            "record={'author': 'agent', 'text': '<в чём уткнулся, что уже пробовал, чего не хватает>'}. "
            f"Меняется ровно 1 файл: {OUTBOX_RELPATH}. Остальные файлы только читаются.")
    return DriveGoal(status="proposed", goal=goal,
                     success_check=f"В {OUTBOX_RELPATH} появилась новая запись с вопросом человеку",
                     drive="stuck_need", action="pursue_goal")
