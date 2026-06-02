"""Memory Hygiene (§4 Memory Governance — cleanup, dedup, expiry, summarise).

Four independent policies, one principle: *cleanup is a deliberate
operation, never a side effect of another action*. Each call returns a
typed report so the caller (CLI, audit log, future scheduler) can record
exactly what was removed and why. Every removal goes through
`PersistentMemoryStore`'s atomic rewrite — there is no partial state.

Policies are intentionally NOT chained together inside the module: the
CLI surface (`:hygiene`) chains them in a deterministic order
(`expire` -> `dedupe` -> `summarise` -> `backups`), and every step
emits its own audit event. Tests can call them individually.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Protocol, Sequence

from core.models import MemoryRecord


# ============================================================
# 1. Backup cleanup
# ============================================================

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


# ============================================================
# 2. Deduplication (write-time + post-hoc)
# ============================================================

DEFAULT_DEDUP_THRESHOLD = 0.85
_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Case-insensitive, whitespace-collapsed comparison key."""
    return _WS_RE.sub(" ", (text or "").strip().lower())


def _similarity(a: str, b: str) -> float:
    """Cheap Jaccard over word sets, then boosted by substring containment.

    The MVP-10 brief explicitly forbids embeddings until proven
    necessary. Word-level Jaccard catches "same fact, different word
    order" and `containment` catches "old record is a prefix of the new
    one". Together they cover the common "user repeats themselves"
    pattern without needing a vector DB.

    Returns 0.0 .. 1.0.
    """
    na, nb = _normalise(a), _normalise(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0

    tokens_a = set(na.split())
    tokens_b = set(nb.split())
    if not tokens_a or not tokens_b:
        return 0.0
    intersection = tokens_a & tokens_b
    union = tokens_a | tokens_b
    jaccard = len(intersection) / len(union)

    # Containment boost: if one normalised string contains the other,
    # this is a near-duplicate even when Jaccard is borderline.
    if na in nb or nb in na:
        shorter = min(len(na), len(nb))
        longer = max(len(na), len(nb))
        containment = shorter / longer
        # Take the max so we never penalise a clear containment match
        # because Jaccard happened to be lower.
        return max(jaccard, containment)

    return jaccard


def find_duplicate(
    text: str,
    existing: Sequence[MemoryRecord],
    threshold: float = DEFAULT_DEDUP_THRESHOLD,
) -> tuple[MemoryRecord, float] | None:
    """Highest-scoring existing record above `threshold`, or None.

    Used by both write-time (refuse to persist a near-duplicate) and
    post-hoc (collapse near-duplicates that already made it to disk).
    """
    if not text or not existing:
        return None
    best: tuple[MemoryRecord, float] | None = None
    for rec in existing:
        rec_text = rec.content if isinstance(rec.content, str) else str(rec.content)
        score = _similarity(text, rec_text)
        if score >= threshold and (best is None or score > best[1]):
            best = (rec, score)
    return best


@dataclass(frozen=True)
class DuplicateGroup:
    canonical_id: str
    canonical_content_preview: str
    duplicate_ids: list[str]


@dataclass
class DedupReport:
    threshold: float
    scanned: int = 0
    groups: list[DuplicateGroup] = field(default_factory=list)
    deleted: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "threshold": self.threshold,
            "scanned": self.scanned,
            "groups": len(self.groups),
            "deleted_count": len(self.deleted),
            "deleted_ids": list(self.deleted),
            "dry_run": self.dry_run,
        }


class _StoreProto(Protocol):
    """Minimal interface deduplicate_memory / expire_memory need."""

    def load(self) -> list[MemoryRecord]: ...
    def _load_raw(self) -> list[MemoryRecord]: ...
    def _rewrite(self, records: list[MemoryRecord]) -> None: ...


def deduplicate_memory(
    store: _StoreProto,
    *,
    threshold: float = DEFAULT_DEDUP_THRESHOLD,
    dry_run: bool = False,
) -> DedupReport:
    """Collapse near-duplicates already on disk.

    The OLDEST record in every duplicate group is treated as canonical
    (oldest = first to be deliberately remembered). Newer near-copies
    are deleted. This makes dedup idempotent: a second run finds zero
    new groups.

    `dry_run=True` returns what WOULD be deleted but rewrites nothing.
    """
    if not 0.0 < threshold <= 1.0:
        raise ValueError(f"threshold must be in (0, 1], got {threshold}")

    records = store.load()
    report = DedupReport(threshold=threshold, scanned=len(records), dry_run=dry_run)

    if len(records) < 2:
        return report

    # Stable sort by creation time, oldest first. Ties broken by id so
    # the result is deterministic even when timestamps collide.
    ordered = sorted(records, key=lambda r: (r.created_at, r.id))

    keep: list[MemoryRecord] = []
    canonical_for_dup: dict[str, MemoryRecord] = {}

    for rec in ordered:
        text = rec.content if isinstance(rec.content, str) else str(rec.content)
        match = find_duplicate(text, keep, threshold=threshold)
        if match is None:
            keep.append(rec)
        else:
            canonical_for_dup[rec.id] = match[0]

    if not canonical_for_dup:
        return report

    # Build the per-canonical group list for the audit trail.
    by_canonical: dict[str, list[str]] = {}
    for dup_id, canon in canonical_for_dup.items():
        by_canonical.setdefault(canon.id, []).append(dup_id)

    for canon_id, dup_ids in by_canonical.items():
        canon = next(r for r in keep if r.id == canon_id)
        canon_text = canon.content if isinstance(canon.content, str) else str(canon.content)
        preview = canon_text[:80] + ("…" if len(canon_text) > 80 else "")
        report.groups.append(
            DuplicateGroup(
                canonical_id=canon_id,
                canonical_content_preview=preview,
                duplicate_ids=sorted(dup_ids),
            )
        )
        report.deleted.extend(sorted(dup_ids))

    report.deleted.sort()
    if not dry_run:
        store._rewrite(keep)
    return report


# ============================================================
# 3. TTL / expiration
# ============================================================

@dataclass
class ExpiryReport:
    scanned: int = 0
    expired: list[str] = field(default_factory=list)   # record ids
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "scanned": self.scanned,
            "expired_count": len(self.expired),
            "expired_ids": list(self.expired),
            "dry_run": self.dry_run,
        }


def _is_expired(record: MemoryRecord, now: datetime) -> bool:
    if record.ttl_seconds is None or record.ttl_seconds <= 0:
        return False
    age = (now - record.created_at).total_seconds()
    return age >= record.ttl_seconds


def expire_memory(
    store: _StoreProto,
    *,
    now: datetime | None = None,
    dry_run: bool = False,
) -> ExpiryReport:
    """Remove records whose `created_at + ttl_seconds` has passed.

    Records with `ttl_seconds=None` (the default) are NEVER expired —
    they were saved as "keep until manually forgotten".
    """
    now = now or datetime.now(timezone.utc)
    # Use _load_raw() to see all records including expired ones — load()
    # would silently evict them before we can report their IDs.
    records = store._load_raw()
    report = ExpiryReport(scanned=len(records), dry_run=dry_run)

    keep: list[MemoryRecord] = []
    for rec in records:
        if _is_expired(rec, now):
            report.expired.append(rec.id)
        else:
            keep.append(rec)

    report.expired.sort()
    if report.expired and not dry_run:
        store._rewrite(keep)
    return report


# ============================================================
# 4. Summarisation
# ============================================================

@dataclass
class SummaryReport:
    tag: str
    scanned: int = 0                 # records considered (after tag filter)
    summarised_ids: list[str] = field(default_factory=list)
    new_record_id: str | None = None
    skipped_reason: str | None = None
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "tag": self.tag,
            "scanned": self.scanned,
            "summarised_count": len(self.summarised_ids),
            "summarised_ids": list(self.summarised_ids),
            "new_record_id": self.new_record_id,
            "skipped_reason": self.skipped_reason,
            "dry_run": self.dry_run,
        }


class _LLMProto(Protocol):
    def complete(
        self,
        system: str,
        user: str,
        max_tokens: int = ...,
        temperature: float = ...,
    ) -> str: ...


SUMMARY_TAG = "summarised"
DEFAULT_SUMMARY_MAX_RECORDS = 10
_SUMMARY_SYSTEM = (
    "You are compressing a list of long-term memory records into ONE "
    "concise note. Preserve every distinct fact, drop redundancy, drop "
    "opinions, drop chronology unless essential. Output plain text, no "
    "bullets unless the source items were already enumerated. Maximum "
    "800 characters."
)


def summarise_memory(
    store: _StoreProto,
    llm: _LLMProto,
    *,
    tag: str,
    max_records: int = DEFAULT_SUMMARY_MAX_RECORDS,
    dry_run: bool = False,
) -> SummaryReport:
    """Merge records sharing `tag` into a single summarised record.

    Behaviour:
      - 0 matching records  -> no-op (skipped_reason='no records')
      - 1 matching record   -> no-op (skipped_reason='single record')
      - 2..max_records      -> LLM called, summary saved, originals removed
      - >max_records        -> only the oldest `max_records` are merged

    On any LLM exception the store is left UNTOUCHED and the report
    carries `skipped_reason='llm_error: <type>: <msg>'`. Records
    carrying `tag == SUMMARY_TAG` are skipped — summaries don't get
    re-summarised on every run.
    """
    if not tag or not tag.strip():
        raise ValueError("tag must be a non-empty string")
    if max_records < 2:
        raise ValueError(f"max_records must be >= 2, got {max_records}")

    tag_norm = tag.strip().lower()
    if tag_norm == SUMMARY_TAG:
        # Refuse to chain-summarise our own output.
        return SummaryReport(
            tag=tag, skipped_reason=f"cannot summarise the '{SUMMARY_TAG}' tag itself",
            dry_run=dry_run,
        )

    records = store.load()
    matching = [r for r in records if tag_norm in {t.lower() for t in (r.tags or [])}]
    # Already-summarised records are excluded.
    matching = [r for r in matching if SUMMARY_TAG not in {t.lower() for t in (r.tags or [])}]

    report = SummaryReport(tag=tag, scanned=len(matching), dry_run=dry_run)

    if not matching:
        report.skipped_reason = "no records"
        return report
    if len(matching) < 2:
        report.skipped_reason = "single record (nothing to merge)"
        return report

    # Oldest first; cap to max_records.
    matching.sort(key=lambda r: (r.created_at, r.id))
    selected = matching[:max_records]

    user_prompt_lines = [
        f"You are summarising {len(selected)} records tagged '{tag}'.",
        "Combine them into ONE record. Keep every distinct fact.",
        "",
    ]
    for i, r in enumerate(selected, 1):
        text = r.content if isinstance(r.content, str) else str(r.content)
        user_prompt_lines.append(f"--- record {i} (id={r.id}) ---")
        user_prompt_lines.append(text)
    user_prompt_lines.append("")
    user_prompt_lines.append("Output the merged summary now:")
    user_prompt = "\n".join(user_prompt_lines)

    try:
        raw = llm.complete(
            system=_SUMMARY_SYSTEM,
            user=user_prompt,
            max_tokens=1024,
            temperature=0.2,
        )
    except Exception as exc:  # noqa: BLE001
        report.skipped_reason = f"llm_error: {type(exc).__name__}: {exc}"
        return report

    summary_text = (raw or "").strip()
    # Hard cap: never let the summary be larger than the originals
    # combined (sanity check against runaway model output).
    combined_chars = sum(
        len(r.content if isinstance(r.content, str) else str(r.content))
        for r in selected
    )
    if not summary_text:
        report.skipped_reason = "llm returned empty summary"
        return report
    if len(summary_text) > max(800, combined_chars):
        summary_text = summary_text[:800].rstrip() + "…"

    # Preserve the canonical tag + add the marker so summarised records
    # are excluded from future summarisation passes.
    new_tags = sorted({tag_norm, SUMMARY_TAG})

    selected_ids = sorted(r.id for r in selected)
    report.summarised_ids = selected_ids

    if dry_run:
        return report

    # Build the kept list: every non-selected record + the new summary.
    selected_id_set = set(selected_ids)
    new_record = MemoryRecord(
        type="semantic",
        content=summary_text,
        tags=new_tags,
        owner="self",
    )
    keep = [r for r in records if r.id not in selected_id_set]
    keep.append(new_record)
    store._rewrite(keep)
    report.new_record_id = new_record.id
    return report


# ============================================================
# 5. Importance-based archiving
# ============================================================
# Records that are NEVER used and have low value move to the archive store
# instead of being deleted. This keeps the active store lean (fast retrieval)
# while preserving everything — like a filing cabinet vs a bin.

# Tag importance weights — higher = more valuable
_TAG_WEIGHTS: dict[str, float] = {
    "decision":     1.0,
    "insight":      0.9,
    "fact":         0.8,
    "preference":   0.8,
    "project":      0.7,
    "user-approved": 0.6,
}

# Archive if importance score is below this threshold
DEFAULT_ARCHIVE_THRESHOLD = 0.25

# Records younger than this (in days) are never archived regardless of score
DEFAULT_ARCHIVE_MIN_AGE_DAYS = 7


@dataclass
class ArchiveReport:
    threshold: float
    min_age_days: int
    scanned: int = 0
    archived: list[str] = field(default_factory=list)   # record ids moved to archive
    dry_run: bool = False

    def summary(self) -> dict:
        return {
            "threshold": self.threshold,
            "min_age_days": self.min_age_days,
            "scanned": self.scanned,
            "archived_count": len(self.archived),
            "archived_ids": list(self.archived),
            "dry_run": self.dry_run,
        }


def _importance_score(record: MemoryRecord, now: datetime) -> float:
    """Score a memory record 0.0-1.0. Higher = more worth keeping active.

    Formula:
      base     = best tag weight (or 0.3 if no known tags)
      access   = +0.05 per access, capped at +0.3 (logarithmic feel)
      recency  = -0.01 per day since last access, capped at -0.3
                  (records never accessed use created_at as reference)
    """
    tags_lower = {t.strip().lower() for t in (record.tags or [])}
    base = max((_TAG_WEIGHTS.get(t, 0.0) for t in tags_lower), default=0.3)

    # Access boost — each use bumps importance
    access_boost = min(0.3, record.access_count * 0.05)

    # Recency penalty — unused records slowly drift toward the archive
    reference_dt = record.last_accessed_at or record.created_at
    days_idle = max(0.0, (now - reference_dt).total_seconds() / 86400)
    recency_penalty = min(0.3, days_idle * 0.01)

    return max(0.0, min(1.0, base + access_boost - recency_penalty))


class _ArchiveStoreProto(Protocol):
    """Minimal interface required by archive_low_value_memory."""

    def load(self) -> list[MemoryRecord]: ...
    def archive_record(self, record_id: str) -> bool: ...
    def _rewrite(self, records: list[MemoryRecord]) -> None: ...


def archive_low_value_memory(
    store: _ArchiveStoreProto,
    *,
    threshold: float = DEFAULT_ARCHIVE_THRESHOLD,
    min_age_days: int = DEFAULT_ARCHIVE_MIN_AGE_DAYS,
    dry_run: bool = False,
    now: datetime | None = None,
) -> ArchiveReport:
    """Move low-value records from active memory to the archive.

    A record is archivable when ALL three conditions hold:
      1. importance_score < threshold
      2. age (since created_at) >= min_age_days
      3. access_count == 0  OR  (days since last access) >= min_age_days

    Records are NEVER deleted — only moved to archive. The archive is a
    permanent reference store (like an old filing cabinet). This ensures
    no knowledge is ever lost, just deprioritised.
    """
    now = now or datetime.now(timezone.utc)
    records = store.load()
    report = ArchiveReport(threshold=threshold, min_age_days=min_age_days,
                           scanned=len(records), dry_run=dry_run)

    for rec in records:
        age_days = (now - rec.created_at).total_seconds() / 86400
        if age_days < min_age_days:
            continue  # too young — give it time

        score = _importance_score(rec, now)
        if score >= threshold:
            continue  # valuable enough to stay active

        # Check last-access recency separately
        if rec.last_accessed_at is not None:
            days_since_access = (now - rec.last_accessed_at).total_seconds() / 86400
            if days_since_access < min_age_days:
                continue  # recently used — keep it active

        report.archived.append(rec.id)

    if not dry_run:
        for record_id in report.archived:
            store.archive_record(record_id)

    return report
