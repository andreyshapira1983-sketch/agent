"""Очередь на суд: итоговые решения агента судит Клод, вне хода и другой моделью.

Слово оператора 25.09: новых ключей нет, пока агент не заработал; судит
Клод из сессии Claude Code, пока она включена. До сих пор каждое «сделано»
судила та же семья моделей, что и работала (DeepSeek), а модель узнаёт
свои тексты и завышает им оценку: Panickssery, Bowman, Feng, «LLM Evaluators
Recognize and Favor Their Own Generations», NeurIPS 2024 (arXiv 2404.13076).
Замер того же дня: из 10 последних «verified» целей ложных было 5.

Поэтому:

* агент кладёт в очередь ПОЛОЖИТЕЛЬНОЕ итоговое решение («цель достигнута»):
  завышение живёт в «да», ложное «нет» стоит только повтора;
* пока Клод не вынес решения, оно предварительное — в зачёт не идёт;
* решения Клода лежат в отдельном журнале, который агент писать не может
  (`core/control_files.CONTROL_RELPATHS`): суд, который подсудимый может
  переписать, не суд;
* согласие «предварительное против Клода» считается — это и есть мера того,
  как часто ошибается собственный судья агента.

Клод выносит решение командой `scripts/judge_queue.py rule …`, открыв сами
файлы-улики: «проверь своими руками», а не по докладу.
"""
from __future__ import annotations

import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)

QUEUE_RELPATH = "data/judge_queue.jsonl"
#: Пишет только Клод (командой мимо инструментов агента) — тормоз оператора.
RULINGS_RELPATH = "data/judge_rulings.jsonl"
RULINGS = ("confirmed", "overturned", "unclear")


def _rows(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    with state_file_lock(path):
        return [r for r in read_state_jsonl_unlocked(path) if isinstance(r, dict)]


def _append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with state_file_lock(path):
        append_state_jsonl_unlocked(path, [row])


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def submit(root: Path | str, *, kind: str, subject: str, provisional: str,
           evidence: list[str] | tuple[str, ...] = (), detail: str = "") -> str:
    """Положить решение на суд; то же решение дважды не кладётся. Вернуть его id."""
    evidence = [str(e) for e in evidence]
    key = json.dumps([kind, subject, provisional, sorted(evidence)], ensure_ascii=False)
    item_id = "jq_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]
    path = Path(root) / QUEUE_RELPATH
    if any(r.get("id") == item_id for r in _rows(path)):
        return item_id
    _append(path, {"id": item_id, "ts": _now(), "kind": kind, "subject": subject,
                   "provisional": provisional, "evidence": evidence, "detail": detail[:2000]})
    return item_id


def rulings(root: Path | str) -> dict[str, dict[str, Any]]:
    return {str(r.get("id")): r for r in _rows(Path(root) / RULINGS_RELPATH)}


def pending(root: Path | str) -> list[dict[str, Any]]:
    ruled = rulings(root)
    return [r for r in _rows(Path(root) / QUEUE_RELPATH) if r.get("id") not in ruled]


def rule(root: Path | str, item_id: str, verdict: str, reason: str, *, judge: str = "claude") -> dict[str, Any]:
    """Вынести решение. Без причины, по неизвестному id или повторно — отказ."""
    if verdict not in RULINGS:
        raise ValueError(f"verdict must be one of {RULINGS}")
    if not reason.strip():
        raise ValueError("a ruling needs its reason: what was opened and what it showed")
    if not any(r.get("id") == item_id for r in _rows(Path(root) / QUEUE_RELPATH)):
        raise KeyError(f"no such item in the judge queue: {item_id}")
    if item_id in rulings(root):
        raise ValueError(f"already ruled: {item_id}")
    row = {"id": item_id, "ts": _now(), "verdict": verdict, "reason": reason.strip(), "judge": judge}
    _append(Path(root) / RULINGS_RELPATH, row)
    return row


def standing(root: Path | str, item_id: str) -> str:
    """confirmed | overturned | unclear | provisional (ещё не судили)."""
    return str(rulings(root).get(item_id, {}).get("verdict") or "provisional")


def agreement(root: Path | str) -> dict[str, dict[str, int]]:
    """По роду решения: сколько подтверждено, отменено, неясно и ждёт суда."""
    ruled = rulings(root)
    out: dict[str, dict[str, int]] = {}
    for item in _rows(Path(root) / QUEUE_RELPATH):
        word = str(ruled.get(str(item.get("id")), {}).get("verdict") or "provisional")
        bucket = out.setdefault(str(item.get("kind")), dict.fromkeys((*RULINGS, "provisional"), 0))
        bucket[word] += 1
    return out
