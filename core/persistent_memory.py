"""Persistent Memory Record store (§4 — long-term, JSONL on disk).

Minimal contract:
  - append-only writes (one MemoryRecord per JSONL line)
  - whole-file load on demand
  - per-id delete + bulk delete
  - corrupted lines are skipped, not fatal
  - low-value records are ARCHIVED (not deleted) — see archive_record()
  - expired records (ttl_seconds set and past) are evicted on load()
"""
from __future__ import annotations

import datetime as _dt
import re
from collections.abc import Iterable
from pathlib import Path

from core.models import MemoryRecord
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)


class PersistentMemoryStore:
    """JSONL-backed list of MemoryRecords.

    Active memory is loaded every cycle. Archive is never injected into
    prompts automatically — it is a reference store for explicit recall.
    """

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        # Archive lives next to the active store with a .archive suffix
        self.archive_path = self.path.with_suffix(".archive.jsonl")

    # ---------- writes ----------

    def save(self, record: MemoryRecord) -> None:
        """Append one record. O(1) write."""
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [record.model_dump(mode="json")])

    def save_many(self, records: Iterable[MemoryRecord]) -> int:
        payloads: list[dict] = []
        for r in records:
            payloads.append(r.model_dump(mode="json"))
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, payloads)
        return len(payloads)

    def update(self, record: MemoryRecord) -> bool:
        """Replace an existing record in-place (full rewrite). Returns True on
        success.
        """
        with state_file_lock(self.path):
            records = self._active_unlocked()
            new_records = []
            updated = False
            for r in records:
                if r.id == record.id:
                    new_records.append(record)
                    updated = True
                else:
                    new_records.append(r)
            if updated:
                rewrite_state_jsonl_unlocked(
                    self.path,
                    [r.model_dump(mode="json") for r in new_records],
                )
        return updated

    def update_many(self, records: Iterable[MemoryRecord]) -> int:
        """Replace several records in ONE rewrite. Returns how many landed."""
        by_id = {r.id: r for r in records}
        if not by_id:
            return 0
        with state_file_lock(self.path):
            current = self._active_unlocked()
            merged: list[MemoryRecord] = []
            landed = 0
            for existing in current:
                replacement = by_id.get(existing.id)
                if replacement is None:
                    merged.append(existing)
                else:
                    merged.append(replacement)
                    landed += 1
            if landed:
                rewrite_state_jsonl_unlocked(
                    self.path,
                    [r.model_dump(mode="json") for r in merged],
                )
        return landed

    def archive_record(self, record_id: str) -> bool:
        """Move a record from active store to archive. Returns True if moved."""
        # The active file stays locked across read, archive-append and rewrite:
        # an append landing in that window used to be dropped by the rewrite.
        with state_file_lock(self.path):
            records = self._active_unlocked()
            target = next((r for r in records if r.id == record_id), None)
            if target is None:
                return False
            target = target.model_copy(update={"archived": True})
            with state_file_lock(self.archive_path):   # different file, own lock
                append_state_jsonl_unlocked(
                    self.archive_path, [target.model_dump(mode="json")],
                )
            keep = [r for r in records if r.id != record_id]
            rewrite_state_jsonl_unlocked(
                self.path, [r.model_dump(mode="json") for r in keep],
            )
        return True

    # ---------- reads ----------

    def load(self) -> list[MemoryRecord]:
        """Full file scan — returns only LIVE (non-expired) records."""
        all_records = self._load_raw()
        now = _dt.datetime.now(_dt.timezone.utc)
        live: list[MemoryRecord] = []
        expired_found = False
        for rec in all_records:
            if self._is_expired(rec, now):
                expired_found = True
                continue
            live.append(rec)
        # Lazy eviction: rewrite store only when expired records are found
        if expired_found:
            self._rewrite(live)
        return live

    def _active_unlocked(self) -> list[MemoryRecord]:
        """Live (non-expired) records, read inside an already-held lock.

        TTL eviction is deliberately NOT performed here: the caller is midway
        through its own rewrite and will write the surviving set itself. A
        second rewrite from inside would race with it.
        """
        now = _dt.datetime.now(_dt.timezone.utc)
        return [rec for rec in self._load_raw_unlocked() if not self._is_expired(rec, now)]

    @staticmethod
    def _is_expired(rec: MemoryRecord, now: _dt.datetime) -> bool:
        """Has this record's TTL run out as of ``now``?

        One predicate, two callers, and they are not interchangeable:
        `active` reads and evicts, `_active_unlocked` reads inside a lock
        its caller already holds and leaves the rewrite to that caller. They
        must agree on WHICH records are alive, or a rewrite would preserve
        rows the read path has already stopped returning — the store would
        keep answering "gone" while never actually letting go.
        """
        if rec.ttl_seconds is None:
            return False
        expires_at = rec.created_at + _dt.timedelta(seconds=rec.ttl_seconds)
        if expires_at.tzinfo is None:
            expires_at = expires_at.replace(tzinfo=_dt.timezone.utc)
        return expires_at <= now

    def _load_raw_unlocked(self) -> list[MemoryRecord]:
        """Same as `_load_raw`, but the caller already holds the file lock."""
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        for row in read_state_jsonl_unlocked(self.path):
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def _load_raw(self) -> list[MemoryRecord]:
        """Load ALL records from disk without TTL filtering."""
        if not self.path.exists():
            return []
        out: list[MemoryRecord] = []
        with state_file_lock(self.path):
            rows = read_state_jsonl_unlocked(self.path)
        for row in rows:
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def load_archive(self) -> list[MemoryRecord]:
        """Load archived records (for explicit recall/inspection only)."""
        if not self.archive_path.exists():
            return []
        out: list[MemoryRecord] = []
        with state_file_lock(self.archive_path):
            rows = read_state_jsonl_unlocked(self.archive_path)
        for row in rows:
            try:
                out.append(MemoryRecord.model_validate(row))
            except ValueError:
                continue
        return out

    def count(self) -> int:
        return len(self.load())

    def count_archive(self) -> int:
        return len(self.load_archive())

    def get(self, record_id: str) -> MemoryRecord | None:
        for r in self.load():
            if r.id == record_id:
                return r
        return None

    # ---------- deletes ----------

    def delete(self, record_id: str) -> bool:
        """Returns True if a record was actually removed.

        One lock over read and rewrite, for the same reason as `update`.
        """
        with state_file_lock(self.path):
            records = self._active_unlocked()
            keep = [r for r in records if r.id != record_id]
            if len(keep) == len(records):
                return False
            rewrite_state_jsonl_unlocked(
                self.path, [r.model_dump(mode="json") for r in keep],
            )
        return True

    def delete_all(self) -> int:
        n = self.count()
        with state_file_lock(self.path):
            if self.path.exists():
                self.path.unlink()
        return n

    # ---------- helpers ----------

    def _rewrite(self, records: list[MemoryRecord]) -> None:
        with state_file_lock(self.path):
            rewrite_state_jsonl_unlocked(
                self.path,
                [record.model_dump(mode="json") for record in records],
            )


    def search_archive(self, term: str, *, limit: int = 10) -> list[MemoryRecord]:
        """Разбудить взглядом, не рукой (MIR-138): поиск по архиву, только чтение.

        86 % памяти жило недостижимым ни одной командой; «отодвинем подальше»
        честно лишь пока из сна можно ЗАГЛЯНУТЬ. Возврат в активную память —
        отдельная власть (MIR-156), не эта функция.
        """
        needle = (term or "").strip().casefold()
        if not needle:
            return []
        hits = [
            record for record in self.load_archive()
            if needle in str(record.content).casefold()
        ]
        hits.sort(key=lambda r: str(r.created_at), reverse=True)
        return hits[:limit]


# ---------- the agent's own write door (his design, his redactions) ----------
# Contract: memory_door_write(store, policy, text, kind, provenance) -> mem_id | None.
# Conclusions pass, raw code / duplicates / frozen sources / unmapped kinds do not.

_CODE_PUNCT_RE = re.compile(r"[:=+\[\](){}<>]|->|;\s*$")
_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё]{2,}")


def _count_code_punct(text):
    return len(_CODE_PUNCT_RE.findall(text))


def _count_words(text):
    return len(_WORD_RE.findall(text))


def _looks_like_code(text):
    words = _count_words(text)
    punct = _count_code_punct(text)
    if punct >= 1 and words <= 8:
        return True
    if words >= 10 and punct == 0:
        return False
    return bool(punct >= 2 and words <= 12)


CONSENT_TAG_MAP = {
    "[ВЫВОД, проверен боем]": "decision",
    "[ВЫВОД, замерен N раз]": "fact",
    "[НАБЛЮДЕНИЕ, один день]": "insight",
}

#: Словарь двери — одной строкой, чтобы его можно было показать тому, кто в
#: дверь стучится (замер 2026-09-03: шесть ходов, kind='lesson'/'reflection',
#: ноль записей — дверь молчала о своих ключах).
ACCEPTED_KINDS_TEXT = "EXACTLY one of: " + ", ".join(repr(k) for k in (
    "[ВЫВОД, проверен боем]", "[ВЫВОД, замерен N раз]", "[НАБЛЮДЕНИЕ, один день]",
))


def memory_door_verdict(store, policy, text, kind, provenance):
    """(mem_id | None, reason) — отказ называет правило, которое сработало.

    До 2026-09-03 дверь на отказ возвращала None без слова, и агент угадывал
    причину (и угадывал неверно). Причина — для того, кто стучится; решение
    двери не меняется.
    """
    text = (text or '').strip()
    kind = (kind or '').strip()
    provenance = (provenance or '').strip()
    if not text or not kind or not provenance:
        missing = [n for n, v in (("text", text), ("kind", kind), ("provenance", provenance)) if not v]
        return None, f"missing required field(s): {', '.join(missing)}"
    if _looks_like_code(text):
        return None, "text looks like code (code punctuation with few words); the door banks prose conclusions only"
    consent_tag = CONSENT_TAG_MAP.get(kind)
    if consent_tag is None:
        return None, f"unknown kind {kind!r}; the door accepts {ACCEPTED_KINDS_TEXT}"
    tags = [kind, provenance, consent_tag]
    if provenance.lower().startswith("memory:"):
        # Authority change 2026-09-04: memory is not a source of memory — a
        # record whose only evidence is another record is the self-echo the
        # antibody exists to stop, one layer earlier.
        return None, "provenance 'memory:…' refused: a memory cannot be the source of a memory"
    # Block 7 (audit W4, 2026-09-03): the door consulted the policy WITHOUT
    # the recent-writes log, so the echo antibody never saw the agent's own
    # door writes and `data/memory_writes.jsonl` was never fed by them.
    registry = _door_write_registry(store)
    recent = []
    if registry is not None:
        try:
            recent = registry.recent()
        except Exception:  # noqa: BLE001 — a registry hiccup must never block the door
            recent = []
    decision = policy.decide(
        text, tags, source="agent-auto", existing=store.load(), recent_writes=recent,
    )
    if decision.decision != "save":
        why = "; ".join(str(r) for r in (getattr(decision, "reasons", None) or ())) or "write policy refused"
        return None, f"write policy refused: {why}"
    record = MemoryRecord(content=text, type="semantic", tags=tags, owner="self", source="agent-auto")
    store.save(record)
    # Authority change 2026-09-04: «stored» is said only after an independent
    # readback finds the id on disk. A write that cannot be read back is not
    # a memory, and the caller must not report DONE on it.
    try:
        found = any(getattr(r, "id", None) == record.id for r in store.load())
    except Exception:  # noqa: BLE001 — an unreadable store cannot confirm a write
        found = False
    if not found:
        return None, "not stored: readback after save did not find the record"
    if registry is not None:
        try:
            from core.memory_echo_antibody import make_event

            registry.append(make_event(text, tags=tags, record_type="semantic", source="agent-auto"))
        except Exception:  # noqa: BLE001, S110 — the write stands; the echo log is best-effort
            pass
    return record.id, ""


def _door_write_registry(store):
    """The recent-writes log beside the store (`data/memory_writes.jsonl`),
    or None for a store without a path."""
    path = getattr(store, "path", None)
    if path is None:
        return None
    try:
        from core.memory_echo_antibody import MemoryWriteRegistry

        return MemoryWriteRegistry(Path(path).parent / "memory_writes.jsonl")
    except Exception:  # noqa: BLE001 — no registry is «no recent writes known»
        return None


def memory_door_write(store, policy, text, kind, provenance):
    return memory_door_verdict(store, policy, text, kind, provenance)[0]
