"""Integrity helpers for small JSONL state stores.

The project keeps several operator-visible state files in JSONL. This module
adds a shared envelope with a per-row SHA-256 checksum, lock-friendly append /
rewrite helpers, legacy-row compatibility, and quarantine for corrupt rows.
"""
from __future__ import annotations

import hashlib
import json
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from core.file_lock import exclusive_file_lock
from core.redaction import redact_dlp_text


INTEGRITY_MARKER = "agent-state-jsonl-v1"


@dataclass(frozen=True)
class StateIntegrityIssue:
    line_no: int
    reason: str
    raw: str


class StateIntegrityError(ValueError):
    """Raised when a checksummed state row fails verification."""


def state_lock_path(path: Path | str) -> Path:
    p = Path(path)
    return p.with_suffix(p.suffix + ".lock")


def encode_state_row(payload: dict[str, Any]) -> str:
    digest = _payload_hash(payload)
    row = {
        "_integrity": {
            "format": INTEGRITY_MARKER,
            "alg": "sha256",
            "hash": digest,
        },
        "payload": payload,
    }
    return json.dumps(row, ensure_ascii=False, sort_keys=True, default=_json_default)


def decode_state_row(line: str) -> dict[str, Any]:
    try:
        row = json.loads(line)
    except json.JSONDecodeError as exc:
        raise StateIntegrityError(f"invalid json: {exc.msg}") from exc
    if not isinstance(row, dict):
        raise StateIntegrityError("state row must be a json object")
    if not _looks_like_envelope(row):
        return row
    payload = row.get("payload")
    if not isinstance(payload, dict):
        raise StateIntegrityError("state envelope payload must be an object")
    integrity = row["_integrity"]
    if integrity.get("format") != INTEGRITY_MARKER:
        raise StateIntegrityError("unknown state envelope format")
    if integrity.get("alg") != "sha256":
        raise StateIntegrityError("unsupported state envelope hash algorithm")
    expected = str(integrity.get("hash") or "")
    actual = _payload_hash(payload)
    if expected != actual:
        raise StateIntegrityError("state row checksum mismatch")
    return payload


def read_state_jsonl(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    with exclusive_file_lock(state_lock_path(p)):
        return read_state_jsonl_unlocked(p)


def read_state_jsonl_unlocked(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    payloads: list[dict[str, Any]] = []
    valid_lines: list[str] = []
    issues: list[StateIntegrityIssue] = []
    for line_no, raw_line in enumerate(p.read_text(encoding="utf-8").splitlines(), start=1):
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            payload = decode_state_row(stripped)
        except StateIntegrityError as exc:
            issues.append(StateIntegrityIssue(line_no=line_no, reason=str(exc), raw=raw_line))
            continue
        payloads.append(payload)
        valid_lines.append(encode_state_row(payload))

    if issues:
        _quarantine_issues(p, issues)
        _atomic_write_lines(p, valid_lines)
    elif valid_lines and _needs_upgrade(p):
        _atomic_write_lines(p, valid_lines)
    return payloads


def append_state_jsonl(path: Path | str, payloads: list[dict[str, Any]]) -> None:
    p = Path(path)
    with exclusive_file_lock(state_lock_path(p)):
        append_state_jsonl_unlocked(p, payloads)


def append_state_jsonl_unlocked(path: Path | str, payloads: list[dict[str, Any]]) -> None:
    if not payloads:
        return
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with p.open("a", encoding="utf-8") as fh:
        for payload in payloads:
            fh.write(encode_state_row(payload) + "\n")


def rewrite_state_jsonl(path: Path | str, payloads: list[dict[str, Any]]) -> None:
    p = Path(path)
    with exclusive_file_lock(state_lock_path(p)):
        rewrite_state_jsonl_unlocked(p, payloads)


def rewrite_state_jsonl_unlocked(path: Path | str, payloads: list[dict[str, Any]]) -> None:
    p = Path(path)
    lines = [encode_state_row(payload) for payload in payloads]
    _atomic_write_lines(p, lines)


def quarantine_dir_for(path: Path | str) -> Path:
    return Path(path).parent / ".quarantine"


@contextmanager
def state_file_lock(path: Path | str) -> Iterator[None]:
    with exclusive_file_lock(state_lock_path(path)):
        yield


def _looks_like_envelope(row: dict[str, Any]) -> bool:
    return isinstance(row.get("_integrity"), dict) and "payload" in row


def _json_default(o: Any) -> Any:
    """Fallback serialiser for types json.dumps can't handle natively."""
    if isinstance(o, datetime):
        return o.isoformat()
    raise TypeError(f"Object of type {type(o).__name__} is not JSON serializable")


def _payload_hash(payload: dict[str, Any]) -> str:
    class _Enc(json.JSONEncoder):
        def default(self, o: Any) -> Any:
            if isinstance(o, datetime):
                return o.isoformat()
            return super().default(o)

    canonical = _Enc(ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(payload)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _atomic_write_lines(path: Path, lines: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")
    tmp.replace(path)


def _quarantine_issues(path: Path, issues: list[StateIntegrityIssue]) -> Path:
    quarantine_dir = quarantine_dir_for(path)
    quarantine_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    quarantine_path = quarantine_dir / f"{path.name}.{stamp}.bad.jsonl"
    with quarantine_path.open("w", encoding="utf-8") as fh:
        for issue in issues:
            fh.write(
                json.dumps(
                    {
                        "source": str(path),
                        "line_no": issue.line_no,
                        "reason": issue.reason,
                        "raw": _redact_quarantine_raw(issue.raw),
                        "quarantined_at": datetime.now(timezone.utc).isoformat(),
                    },
                    ensure_ascii=False,
                    sort_keys=True,
                )
                + "\n"
            )
    return quarantine_path


def _needs_upgrade(path: Path) -> bool:
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        try:
            row = json.loads(stripped)
        except json.JSONDecodeError:
            return False
        return not (isinstance(row, dict) and _looks_like_envelope(row))
    return False


def _redact_quarantine_raw(raw: str) -> str:
    redacted, _, _ = redact_dlp_text(raw)
    return redacted
