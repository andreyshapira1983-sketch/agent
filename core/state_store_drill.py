"""Live state-store recovery drill for operator readiness checks.

The drill uses an isolated file under ``data/state_store_drills``. It never
corrupts production state stores; it only proves that the shared integrity
layer can quarantine bad rows and leave a valid active JSONL file behind.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4

from core.state_integrity import (
    decode_state_row,
    encode_state_row,
    quarantine_dir_for,
    read_state_jsonl,
)


@dataclass(frozen=True)
class StateStoreDrillReport:
    status: str
    path: str
    quarantine_dir: str
    recovered_rows: int
    quarantined_files: int
    active_file_integrity_ok: bool
    secret_redacted: bool
    checks: dict[str, bool]
    warnings: list[str]

    def to_dict(self) -> dict:
        return {
            "status": self.status,
            "path": self.path,
            "quarantine_dir": self.quarantine_dir,
            "recovered_rows": self.recovered_rows,
            "quarantined_files": self.quarantined_files,
            "active_file_integrity_ok": self.active_file_integrity_ok,
            "secret_redacted": self.secret_redacted,
            "checks": dict(self.checks),
            "warnings": list(self.warnings),
        }

    def user_summary(self) -> str:
        lines = [
            "=== state-store recovery drill ===",
            f"status={self.status}",
            f"path={self.path}",
            f"quarantine_dir={self.quarantine_dir}",
            f"recovered_rows={self.recovered_rows}",
            f"quarantined_files={self.quarantined_files}",
            f"active_file_integrity_ok={self.active_file_integrity_ok}",
            f"secret_redacted={self.secret_redacted}",
        ]
        lines.append("checks:")
        for name, ok in self.checks.items():
            lines.append(f"  - {name}: {ok}")
        if self.warnings:
            lines.append("warnings:")
            lines.extend(f"  - {item}" for item in self.warnings)
        return "\n".join(lines)


def run_state_store_drill(workspace: Path) -> StateStoreDrillReport:
    drill_id = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    drill_id = f"{drill_id}-{uuid4().hex[:8]}"
    drill_dir = workspace / "data" / "state_store_drills"
    drill_dir.mkdir(parents=True, exist_ok=True)
    path = drill_dir / f"state_drill_{drill_id}.jsonl"

    good_payload = {"kind": "state_store_drill", "id": drill_id, "ok": True}
    mismatched = encode_state_row({"kind": "state_store_drill", "value": "original"})
    mismatched = mismatched.replace("original", "tampered")
    path.write_text(
        "\n".join(
            [
                encode_state_row(good_payload),
                "not-json API_KEY=drillsecret123 andre@example.com",
                mismatched,
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    recovered = read_state_jsonl(path)
    quarantine_dir = quarantine_dir_for(path)
    quarantine_files = sorted(quarantine_dir.glob(f"{path.name}.*.bad.jsonl"))
    quarantine_text = "\n".join(
        item.read_text(encoding="utf-8", errors="replace") for item in quarantine_files
    )
    active_file_integrity_ok = _active_file_integrity_ok(path)
    secret_redacted = (
        "drillsecret123" not in quarantine_text
        and "andre@example.com" not in quarantine_text
        and "[REDACTED:" in quarantine_text
    )
    checks = {
        "good_row_recovered": recovered == [good_payload],
        "bad_rows_quarantined": len(quarantine_files) >= 1,
        "active_file_rewritten": active_file_integrity_ok,
        "quarantine_redacted": secret_redacted,
    }
    warnings = []
    if len(quarantine_files) != 1:
        warnings.append(
            f"expected exactly 1 quarantine file for this drill, found {len(quarantine_files)}"
        )
    status = "passed" if all(checks.values()) else "failed"
    return StateStoreDrillReport(
        status=status,
        path=str(path),
        quarantine_dir=str(quarantine_dir),
        recovered_rows=len(recovered),
        quarantined_files=len(quarantine_files),
        active_file_integrity_ok=active_file_integrity_ok,
        secret_redacted=secret_redacted,
        checks=checks,
        warnings=warnings,
    )


def _active_file_integrity_ok(path: Path) -> bool:
    try:
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    except OSError:
        return False
    if not lines:
        return False
    for line in lines:
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            return False
        if not isinstance(row, dict) or "_integrity" not in row:
            return False
        try:
            decode_state_row(line)
        except Exception:
            return False
    return True
