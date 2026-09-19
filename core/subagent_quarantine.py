"""Карантин находок субагента: запись есть, права влиять — нет.

Замер, отвергнутые варианты и границы: MIR-157 в docs/audit/MASTER_ISSUE_REGISTRY.md.
"""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.ids import new_id

#: Хранилище карантина. Отдельный файл, а не память: попав в память, запись
#: получила бы её полномочия, а весь смысл карантина в том, что их нет.
QUARANTINE_RELPATH = "data/subagent_quarantine.jsonl"

#: Полномочие записи карантина — ровно одно и неизменное. Поле пишется явно,
#: потому что умолчание уже однажды раздало человеческий голос (MIR-151).
QUARANTINE_AUTHORITY = "none"

#: Потолок на текст находки: карантин — место для улики, а не второй архив.
MAX_ANSWER_CHARS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quarantine_path(workspace: Path | str) -> Path:
    return Path(workspace) / QUARANTINE_RELPATH


def quarantine_finding(workspace: Path | str, result: Any) -> dict[str, Any]:
    """Положить находку субагента в карантин и вернуть записанную строку.

    Никогда не роняет вызывающего: потерянная находка хуже, чем упавший
    родитель, ровно наоборот — упавший родитель хуже потерянной находки.
    """
    from core.redaction import redact_payload
    from core.state_integrity import append_state_jsonl

    answer = str(getattr(result, "answer", "") or "")
    truncated = len(answer) > MAX_ANSWER_CHARS
    row: dict[str, Any] = {
        "id": new_id("sqf"),
        "ts": _now_iso(),
        # Происхождение: кто спрашивал, о чём, и в какой трассе это искать.
        "contract_name": str(getattr(result, "contract_name", "") or ""),
        "role": str(getattr(result, "role", "") or ""),
        "objective": str(getattr(result, "objective", "") or ""),
        "trace_id": str(getattr(result, "trace_id", "") or ""),
        "status": str(getattr(result, "status", "") or ""),
        # Сигнал доверия, который уже считается бегуном: сколько внешнего
        # субагент принёс сам, а не сочинил.
        "external_evidence_count": int(getattr(result, "external_evidence_count", 0) or 0),
        "external_evidence_kinds": list(getattr(result, "external_evidence_kinds", ()) or ()),
        "answer": answer[:MAX_ANSWER_CHARS],
        "answer_truncated": truncated,
        "authority": QUARANTINE_AUTHORITY,
    }
    payload = redact_payload(row)
    if not isinstance(payload, dict):
        payload = row
    try:
        append_state_jsonl(quarantine_path(workspace), [payload])
    except OSError:
        return payload
    return payload


def load_quarantine(workspace: Path | str) -> list[dict[str, Any]]:
    """Все находки в карантине, старейшие первыми; нечитаемое — пусто."""
    from core.state_integrity import read_state_jsonl

    path = quarantine_path(workspace)
    if not path.is_file():
        return []
    try:
        return [dict(row) for row in read_state_jsonl(path)]
    except (OSError, ValueError):
        return []


def quarantine_status_lines(workspace: Path | str) -> list[str]:
    """Строка о накопленных находках — иначе карантин станет складом без читателя.

    Только чтение и никогда не бросает.
    """
    try:
        rows = load_quarantine(workspace)
        if not rows:
            return []
        grounded = sum(1 for r in rows if int(r.get("external_evidence_count") or 0) > 0)
        oldest = min(str(r.get("ts") or "") for r in rows)[:10]
        line = (
            f"[SUBAGENT] {len(rows)} finding(s) in quarantine, {grounded} with "
            f"external evidence, oldest {oldest}; none may influence a decision"
        )
    except Exception:  # noqa: BLE001 — строка состояния не стоит тика
        return []
    return [line]
