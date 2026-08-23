"""Integrity helpers for small JSONL state stores."""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

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


def _decoded_lines(path: Path) -> Iterator[tuple[int, str | None, str]]:
    """Yield ``(line_no, text_or_None, raw)`` for every line of a state file.

    Whole-file decoding is the FAST PATH and is byte-for-byte what this reader
    always did — a healthy file takes it and nothing below runs. The fallback
    exists because a process killed mid-append can cut a multi-byte character
    in half, and `read_text` then raises before any per-line handling: the
    quarantine machinery built for damaged rows never got to run, and the
    store was unreadable on EVERY later read. Measured: about 9% of cut points
    in a row carrying Cyrillic land inside a character, and this repository
    writes Russian into its state by the operator's own rule.

    A line that does not decode STRICTLY is surfaced as `None` rather than
    repaired with replacement characters: rows predating the checksum envelope
    carry no hash, so a substituted character would pass as a value. An
    undecodable line is quarantined whole, like any other unreadable row.
    """
    try:
        text: str | None = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        text = None
    if text is not None:
        for line_no, raw_line in enumerate(text.splitlines(), start=1):
            yield line_no, raw_line, raw_line
        return
    for line_no, raw_bytes in enumerate(path.read_bytes().split(b"\n"), start=1):
        try:
            yield line_no, raw_bytes.decode("utf-8"), raw_bytes.decode("utf-8")
        except UnicodeDecodeError as exc:
            yield line_no, None, f"{exc.reason} at byte {exc.start}: {raw_bytes[:200]!r}"


def read_state_jsonl_unlocked(path: Path | str) -> list[dict[str, Any]]:
    p = Path(path)
    if not p.exists():
        return []
    payloads: list[dict[str, Any]] = []
    valid_lines: list[str] = []
    issues: list[StateIntegrityIssue] = []
    for line_no, text, raw_line in _decoded_lines(p):
        if text is None:
            issues.append(StateIntegrityIssue(
                line_no=line_no, reason=f"invalid utf-8: {raw_line}", raw=raw_line))
            continue
        stripped = text.strip()
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


def backup_state_file(path: Path | str) -> Path:
    """Copy *path* to ``<path>.<YYYYMMDDTHHMMSSZ>.bak`` and return the copy."""
    p = Path(path)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    target = p.with_suffix(p.suffix + f".{stamp}.bak")
    shutil.copy2(p, target)
    return target


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
    """Replace *path* with *lines*, durably.

    H-06 in `docs/audit/HISTORICAL_FAILURE_LEDGER.md` — the crash-consistency
    class (ext3/ext4 rename semantics; Pillai et al., «All File Systems Are
    Not Created Equal», OSDI 2014). Temp-then-rename buys atomicity of the
    DIRECTORY ENTRY; it does not by itself guarantee the file's DATA reached
    the disk. On a host crash or power loss the renamed file can be empty or
    partially written — and an empty state file is the worst outcome here,
    because it reads as a legitimately empty store: no tasks, no memory,
    nothing to check. The per-row checksums cannot help; there is nothing
    left to checksum.

    Measured cost of the barrier on this machine: +1.3–1.7 ms per write at
    142 / 1000 / 5155 rows, against a tick that spends seconds in provider
    calls. A refused `fsync` is not fatal — the write stays atomic in the
    directory sense, which is exactly what we had before.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", encoding="utf-8") as fh:
        for line in lines:
            fh.write(line + "\n")
        fh.flush()
        try:
            os.fsync(fh.fileno())
        except OSError:  # pragma: no cover — filesystem refuses the barrier
            pass
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
    for _, text, _ in _decoded_lines(path):
        if text is None:
            return False
        stripped = text.strip()
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
