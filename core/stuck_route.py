"""Уткнулся — собери улики и реши; человека — только когда шаг не твой (core/own_decisions.py).

Застревание — величина: одинаковые пустые циклы и дефекты без зелёной правки поднимают драйв
`stuck_need`; его цель детерминирована и проверяется машиной (stuck_goal)."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

OUTBOX_RELPATH = "data/chat_outbox.jsonl"
#: Начало цели «спроси». Её собственный провал считается, но не цитируется:
#: 24.09 каждый провал вкладывал её текст в следующую — «Ты застрял: цель «Ты
#: застрял: цель «Почини…»»», а предмет уходил за обрезку в 120 символов.
#: 2026-09-25: без слова «застрял». Работа Anthropic (arXiv 2604.07729): внутреннее
#: «отчаяние» модели включают провалы и нехватка бюджета, и оно причинно ведёт к
#: подгонке результата; строка «ты застрял» в подсказке — тот же нажим текстом.
#: Прежняя приставка остаётся опознаваемой в старых строках журнала.
STUCK_PREFIX = "Цель не продвигается:"
_STUCK_PREFIXES = (STUCK_PREFIX, "Ты застрял:")
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
            if goal.startswith(_STUCK_PREFIXES):
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
    """Цель «уткнулся»: собрать улики и записать решение; успех — это решение по номеру."""
    import hashlib

    from core.drive_goal import DriveGoal
    from core.own_decisions import DECISIONS_RELPATH, rule_text

    evidence = stuck_evidence(root)
    if not evidence:
        return None
    facts = "; ".join(evidence)
    rows = len(_rows(Path(root) / "data" / "campaign_ledger.jsonl"))
    rid = "reshenie-" + hashlib.sha256(f"{facts}|{rows}".encode()).hexdigest()[:8]
    goal = (f"{STUCK_PREFIX} {facts}. Сначала собери, что уже известно: твои улики (журналы, следы, "
            "прочитанный код) и, если их мало, первоисточник в интернете (web_search и web_fetch). "
            "Затем реши, что делаешь дальше, и запиши решение: journal_append "
            f"path='{DECISIONS_RELPATH}', record={{'id': '{rid}', 'about': '<отпечаток дефекта или цель>', "
            "'decision': '<что именно делаешь дальше>', 'because': '<улика: путь, строка или ссылка>'}. "
            f"{rule_text()} Если следующий шаг не твой — запиши и это решением ('decision': "
            "'спрашиваю человека', 'because': '<какой из этих случаев>') и задай вопрос голосом: "
            f"journal_append path='{OUTBOX_RELPATH}', record={{'author': 'agent', 'reason': 'stuck', "
            "'text': '<что известно, чего не хватает, и один вопрос да/нет или выбором>'}. "
            f"Меняется ровно 1 файл: {DECISIONS_RELPATH} (голос — только в этом случае).")
    return DriveGoal(status="proposed", goal=goal,
                     success_check=f'В {DECISIONS_RELPATH} записано решение "id": "{rid}"',
                     drive="stuck_need", action="pursue_goal")
