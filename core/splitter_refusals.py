"""Journal of splitter refusals: pauses a file the splitter cannot cut until its content changes."""
from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

from core.state_integrity import append_state_jsonl, read_state_jsonl

JOURNAL = Path("data") / "splitter_refusals.jsonl"


def _fingerprint(workspace: str | Path, target: str) -> str | None:
    try:
        return hashlib.sha256((Path(workspace) / target).read_bytes()).hexdigest()
    except OSError:
        return None


def record_refusal(workspace: str | Path, target: str, reason: str) -> None:
    """Запомнить отказ вместе с отпечатком того текста, который видел раскольщик."""
    fingerprint = _fingerprint(workspace, target)
    if fingerprint is None:
        return
    append_state_jsonl(Path(workspace) / JOURNAL, [{
        "target": target.replace("\\", "/"),
        "content_sha256": fingerprint,
        "reason": reason[:300],
        "decided_by": "splitter",
        "ts": datetime.now(timezone.utc).isoformat(),
    }])


def refused_unchanged(workspace: str | Path) -> frozenset[str]:
    """Файлы, которые раскольщик отказался резать и которые с тех пор не менялись."""
    path = Path(workspace) / JOURNAL
    if not path.exists():
        return frozenset()
    latest: dict[str, str] = {}
    for row in read_state_jsonl(path):
        target, fingerprint = row.get("target"), row.get("content_sha256")
        if target and fingerprint:
            latest[str(target)] = str(fingerprint)
    return frozenset(t for t, fp in latest.items() if _fingerprint(workspace, t) == fp)
