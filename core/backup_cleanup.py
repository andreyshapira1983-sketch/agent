"""Уборка резервных копий `.bak.<ts>` из рабочего каталога.

Приехало из `core/hygiene.py`. Тот файл назывался «гигиеной памяти», но эта
политика к памяти отношения не имеет: она удаляет ФАЙЛЫ, которые оставляет за
собой линия самоприменения, и зовут её `core/loop_repair.py` и
`core/state_integrity.py` — не память. Общим у неё с соседями было одно слово
«cleanup» в заголовке.

Принцип тот же и он важен: уборка — намеренная операция, а не побочный эффект
чего-то другого. Активный файл не трогается никогда, а отчёт возвращается
типизированным, чтобы вызывающий записал, что именно удалено и почему.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Matches FileWriteTool's `<path>.bak.<YYYYMMDDTHHMMSSZ>` pattern.
# Captures group 1 = the target filename, group 2 = the timestamp.
BACKUP_NAME_RE = re.compile(r"^(?P<target>.+)\.bak\.(?P<ts>\d{8}T\d{6}Z)$")

# Retention defaults — conservative on purpose. Even a very old single
# backup is preserved by the `keep_last` floor, because a sole backup is
# usually the most valuable kind.
DEFAULT_KEEP_LAST = 3

DEFAULT_MAX_AGE_DAYS = 14

@dataclass(frozen=True)
class BackupCandidate:
    path: Path             # absolute path on disk
    target_name: str       # the file the backup belongs to (without .bak.<ts>)
    ts: datetime           # parsed from the suffix (tz-aware UTC)

@dataclass
class BackupCleanupReport:
    workspace_root: Path
    keep_last: int
    max_age_days: int
    scanned: int = 0
    deleted: list[str] = field(default_factory=list)   # workspace-relative paths
    kept: list[str] = field(default_factory=list)      # workspace-relative paths
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "workspace_root": str(self.workspace_root),
            "keep_last": self.keep_last,
            "max_age_days": self.max_age_days,
            "scanned": self.scanned,
            "deleted_count": len(self.deleted),
            "kept_count": len(self.kept),
            "dry_run": self.dry_run,
            "deleted": list(self.deleted),
        }

def _parse_backup_ts(stem: str) -> datetime | None:
    """Decode `YYYYMMDDTHHMMSSZ` into a tz-aware UTC datetime."""
    try:
        return datetime.strptime(stem, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc)
    except ValueError:
        return None

def _scan_backups(workspace_root: Path) -> list[BackupCandidate]:
    """Walk the workspace and collect every `.bak.<ts>` file we recognise.

    Files whose suffix doesn't parse are ignored — we never touch a file
    we don't fully understand.
    """
    out: list[BackupCandidate] = []
    if not workspace_root.exists():
        return out
    for path in workspace_root.rglob("*.bak.*"):
        if not path.is_file():
            continue
        m = BACKUP_NAME_RE.match(path.name)
        if not m:
            continue
        ts = _parse_backup_ts(m.group("ts"))
        if ts is None:
            continue
        out.append(
            BackupCandidate(path=path, target_name=m.group("target"), ts=ts)
        )
    return out

def cleanup_backups(
    workspace_root: Path,
    *,
    keep_last: int = DEFAULT_KEEP_LAST,
    max_age_days: int = DEFAULT_MAX_AGE_DAYS,
    now: datetime | None = None,
    dry_run: bool = False,
) -> BackupCleanupReport:
    """Remove old `.bak.<ts>` files; never touch the active file itself.

    Retention rule — a backup is DELETED only when BOTH hold:
      - more than `keep_last` newer backups exist for the same target
      - the backup is older than `max_age_days`

    The newest `keep_last` backups per target are always kept regardless
    of age. The cleanest backup is sometimes the only one — so a sole
    survivor is never removed.

    `dry_run=True` returns the same report but performs no deletions.
    """
    if keep_last < 0:
        raise ValueError(f"keep_last must be >= 0, got {keep_last}")
    if max_age_days < 0:
        raise ValueError(f"max_age_days must be >= 0, got {max_age_days}")

    workspace_root = Path(workspace_root).resolve()
    now = now or datetime.now(timezone.utc)
    cutoff = now - _timedelta_days(max_age_days)

    candidates = _scan_backups(workspace_root)
    report = BackupCleanupReport(
        workspace_root=workspace_root,
        keep_last=keep_last,
        max_age_days=max_age_days,
        scanned=len(candidates),
        dry_run=dry_run,
    )

    # Group by (parent_dir, target_name) so identically-named files in
    # different sub-folders don't get pooled together.
    groups: dict[tuple[Path, str], list[BackupCandidate]] = {}
    for c in candidates:
        groups.setdefault((c.path.parent, c.target_name), []).append(c)

    for _key, group in groups.items():
        # Newest first.
        group.sort(key=lambda c: c.ts, reverse=True)
        # Keep the newest keep_last unconditionally.
        protected = group[:keep_last]
        rest = group[keep_last:]
        # Among the unprotected, anything older than cutoff is deleted.
        for c in rest:
            rel = _relative_or_absolute(c.path, workspace_root)
            if c.ts < cutoff:
                if not dry_run:
                    try:
                        c.path.unlink()
                    except OSError:
                        # Treat as kept so we don't lie in the audit log.
                        report.kept.append(rel)
                        continue
                report.deleted.append(rel)
            else:
                report.kept.append(rel)
        for c in protected:
            report.kept.append(_relative_or_absolute(c.path, workspace_root))

    # Sort for deterministic reports.
    report.deleted.sort()
    report.kept.sort()
    return report

def _timedelta_days(days: int):
    from datetime import timedelta
    return timedelta(days=days)

def _relative_or_absolute(path: Path, root: Path) -> str:
    """Best-effort workspace-relative string (falls back to absolute)."""
    try:
        return str(path.relative_to(root))
    except ValueError:
        return str(path)
