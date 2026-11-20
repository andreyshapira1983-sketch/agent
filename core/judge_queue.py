"""Judge queue: the agent's final decisions are judged by Claude, outside the turn and by a different model.

    The agent queues POSITIVE final decisions ("goal reached"); until Claude rules
    they are provisional and do not count. Rulings live in a separate journal the
    agent cannot write (`core/control_files.CONTROL_RELPATHS`). Claude rules via
    `scripts/judge_queue.py rule ...` after opening the evidence files."""
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
#: Written only by Claude (command outside the agent's tools), an operator brake.
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
    """Queue a decision for judging; the same decision is not queued twice. Returns its id."""
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
    """Rule on a decision. Refused without a reason, for an unknown id, or repeated."""
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
    """standing: confirmed | overturned | unclear | provisional (not yet judged)."""
    return str(rulings(root).get(item_id, {}).get("verdict") or "provisional")


def agreement(root: Path | str) -> dict[str, dict[str, int]]:
    """By decision kind: how many are confirmed, overturned, unclear or awaiting judgment."""
    ruled = rulings(root)
    out: dict[str, dict[str, int]] = {}
    for item in _rows(Path(root) / QUEUE_RELPATH):
        word = str(ruled.get(str(item.get("id")), {}).get("verdict") or "provisional")
        bucket = out.setdefault(str(item.get("kind")), dict.fromkeys((*RULINGS, "provisional"), 0))
        bucket[word] += 1
    return out
