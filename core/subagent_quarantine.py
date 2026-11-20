"""Quarantine of subagent findings: recorded, but with no right to influence anything."""
from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.ids import new_id

#: Quarantine store: a separate file, not memory, because a record in memory would acquire its authority.
QUARANTINE_RELPATH = "data/subagent_quarantine.jsonl"

#: The quarantine record's authority is exactly one and unchanging; written explicitly because a default once handed out a human voice.
QUARANTINE_AUTHORITY = "none"

#: Cap on finding text: quarantine holds evidence, not a second archive.
MAX_ANSWER_CHARS = 2000


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def quarantine_path(workspace: Path | str) -> Path:
    return Path(workspace) / QUARANTINE_RELPATH


def quarantine_finding(workspace: Path | str, result: Any) -> dict[str, Any]:
    """Put a subagent finding into quarantine and return the row written; never raises (a crashed parent is worse than a lost finding)."""
    from core.redaction import redact_payload
    from core.state_integrity import append_state_jsonl

    answer = str(getattr(result, "answer", "") or "")
    truncated = len(answer) > MAX_ANSWER_CHARS
    row: dict[str, Any] = {
        "id": new_id("sqf"),
        "ts": _now_iso(),
        # Origin: who asked, about what, and where to look in the trace.
        "contract_name": str(getattr(result, "contract_name", "") or ""),
        "role": str(getattr(result, "role", "") or ""),
        "objective": str(getattr(result, "objective", "") or ""),
        "trace_id": str(getattr(result, "trace_id", "") or ""),
        "status": str(getattr(result, "status", "") or ""),
        # Trust signal the runner already counts: how much external evidence the subagent brought rather than invented.
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
    """All findings in quarantine, oldest first; unreadable means empty."""
    from core.state_integrity import read_state_jsonl

    path = quarantine_path(workspace)
    if not path.is_file():
        return []
    try:
        return [dict(row) for row in read_state_jsonl(path)]
    except (OSError, ValueError):
        return []


def quarantine_status_lines(workspace: Path | str) -> list[str]:
    """A line about accumulated findings, so quarantine does not become a warehouse with no reader; read-only, never raises."""
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
    except Exception:  # noqa: BLE001 — a status line is not worth a tick
        return []
    return [line]
