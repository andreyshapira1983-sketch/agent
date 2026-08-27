"""Уборка резервных копий `.bak.<ts>` из рабочего каталога."""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

# Matches FileWriteTool's `<path>.bak.<YYYYMMDDTHHMMSSZ>` pattern.
# Captures group 1 = the target filename, group 2 = the timestamp.
BACKUP_NAME_RE = re.compile(r"^(?P<target>.+)\.bak\.(?P<ts>\d{8}T\d{6}Z)$")

# Живые семьи имён по перемеру 2026-08-27 (MIR-125): разовые миграции пишут
# три ДРУГИХ формата, и ни один не узнавался — data/ удвоился их мусором.
# Порядок важен: формы с меткой времени раньше бессрочной. Возраст бессрочной
# берётся из mtime файла. Неузнанное имя по-прежнему не трогается.
_BACKUP_NAME_RES: tuple[re.Pattern[str], ...] = (
    BACKUP_NAME_RE,                                                  # t.bak.<ts>
    re.compile(r"^(?P<target>.+)\.(?P<ts>\d{8}T\d{6}Z)\.bak$"),      # t.<ts>.bak
    re.compile(                                                      # t.pre-<slug>-<ts>.bak
        r"^(?P<target>.+)\.pre-[A-Za-z0-9_-]+-(?P<ts>\d{8}T\d{6}Z)\.bak$"),
    re.compile(r"^(?P<target>.+)\.pre-[A-Za-z0-9_.-]+\.bak$"),       # t.pre-<slug>.bak (без метки)
)

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

def _candidate_from_name(path: Path) -> BackupCandidate | None:
    """Узнать файл по одной из живых семей имён; неузнанное — None."""
    for pattern in _BACKUP_NAME_RES:
        m = pattern.match(path.name)
        if not m:
            continue
        raw_ts = m.groupdict().get("ts")
        if raw_ts is not None:
            ts = _parse_backup_ts(raw_ts)
            if ts is None:
                return None
        else:
            try:
                ts = datetime.fromtimestamp(path.stat().st_mtime, tz=timezone.utc)
            except OSError:
                return None
        return BackupCandidate(path=path, target_name=m.group("target"), ts=ts)
    return None


def _scan_backups(workspace_root: Path) -> list[BackupCandidate]:
    """Walk the workspace and collect every backup file we recognise.

    Files whose name doesn't parse as a known backup family are ignored —
    we never touch a file we don't fully understand.
    """
    out: list[BackupCandidate] = []
    if not workspace_root.exists():
        return out
    seen: set[Path] = set()
    for glob in ("*.bak.*", "*.bak"):
        for path in workspace_root.rglob(glob):
            if not path.is_file() or path in seen:
                continue
            seen.add(path)
            candidate = _candidate_from_name(path)
            if candidate is not None:
                out.append(candidate)
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
