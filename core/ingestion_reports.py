from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class IngestReport:
    mode: str
    requested_path: str
    files_seen: int = 0
    files_ingested: int = 0
    files_skipped: int = 0
    bytes_read: int = 0
    chunks: int = 0
    source_count: int = 0
    claim_count: int = 0
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False
    auto_write_memory: bool = False
    ingested_paths: list[str] = field(default_factory=list)
    skipped_paths: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "requested_path": self.requested_path,
            "files_seen": self.files_seen,
            "files_ingested": self.files_ingested,
            "files_skipped": self.files_skipped,
            "bytes_read": self.bytes_read,
            "chunks": self.chunks,
            "source_count": self.source_count,
            "claim_count": self.claim_count,
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "conflicts": self.conflicts,
            "dry_run": self.dry_run,
            "auto_write_memory": self.auto_write_memory,
            "ingested_paths": list(self.ingested_paths),
            "skipped_paths": list(self.skipped_paths[:20]),
            "error_count": len(self.errors),
            "errors": list(self.errors[:20]),
        }

    def user_summary(self) -> str:
        store = self.source_store or {}
        parts = [
            f"ingest {self.mode}: files={self.files_ingested}/{self.files_seen}",
            f"chunks={self.chunks}",
            f"sources={self.source_count}",
            f"claims={self.claim_count}",
            f"saved_sources={store.get('sources_saved', 0)}",
            f"saved_claims={store.get('claims_saved', 0)}",
            f"memory_saved={self.memory_saved}",
            f"memory_skipped={self.memory_skipped}",
            f"memory_rejected={self.memory_rejected}",
            f"conflicts={self.conflicts}",
        ]
        if self.dry_run:
            parts.append("dry_run=True")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        return "(" + "; ".join(parts) + ")"


@dataclass
class WebIngestReport:
    mode: str
    topic: str
    source_ids: list[str] = field(default_factory=list)
    searches: int = 0
    search_results: int = 0
    pages_fetched: int = 0
    bytes_read: int = 0
    chunks: int = 0
    source_count: int = 0
    claim_count: int = 0
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False
    auto_write_memory: bool = False
    fetched_urls: list[str] = field(default_factory=list)
    skipped_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "topic": self.topic,
            "source_ids": list(self.source_ids),
            "searches": self.searches,
            "search_results": self.search_results,
            "pages_fetched": self.pages_fetched,
            "bytes_read": self.bytes_read,
            "chunks": self.chunks,
            "source_count": self.source_count,
            "claim_count": self.claim_count,
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "conflicts": self.conflicts,
            "dry_run": self.dry_run,
            "auto_write_memory": self.auto_write_memory,
            "fetched_urls": list(self.fetched_urls),
            "skipped_urls": list(self.skipped_urls[:20]),
            "error_count": len(self.errors),
            "errors": list(self.errors[:20]),
        }

    def user_summary(self) -> str:
        store = self.source_store or {}
        parts = [
            f"ingest web: topic={self.topic!r}",
            f"sources={','.join(self.source_ids) or '-'}",
            f"searches={self.searches}",
            f"results={self.search_results}",
            f"pages={self.pages_fetched}",
            f"claims={self.claim_count}",
            f"saved_sources={store.get('sources_saved', 0)}",
            f"saved_claims={store.get('claims_saved', 0)}",
            f"memory_saved={self.memory_saved}",
            f"memory_skipped={self.memory_skipped}",
            f"conflicts={self.conflicts}",
        ]
        if self.dry_run:
            parts.append("dry_run=True")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        if self.fetched_urls:
            parts.append(f"fetched={self.fetched_urls[:5]}")
        return "(" + "; ".join(parts) + ")"


@dataclass
class RssIngestReport:
    mode: str
    feed_url: str
    entries_seen: int = 0
    entries_ingested: int = 0
    bytes_read: int = 0
    source_count: int = 0
    claim_count: int = 0
    source_store: dict[str, int] = field(default_factory=dict)
    memory_saved: int = 0
    memory_rejected: int = 0
    memory_skipped: int = 0
    conflicts: int = 0
    dry_run: bool = False
    auto_write_memory: bool = False
    feed_title: str = ""
    entry_urls: list[str] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "mode": self.mode,
            "feed_url": self.feed_url,
            "entries_seen": self.entries_seen,
            "entries_ingested": self.entries_ingested,
            "bytes_read": self.bytes_read,
            "source_count": self.source_count,
            "claim_count": self.claim_count,
            "source_store": dict(self.source_store),
            "memory_saved": self.memory_saved,
            "memory_rejected": self.memory_rejected,
            "memory_skipped": self.memory_skipped,
            "conflicts": self.conflicts,
            "dry_run": self.dry_run,
            "auto_write_memory": self.auto_write_memory,
            "feed_title": self.feed_title,
            "entry_urls": list(self.entry_urls),
            "error_count": len(self.errors),
            "errors": list(self.errors[:20]),
        }

    def user_summary(self) -> str:
        store = self.source_store or {}
        parts = [
            f"ingest rss: url={self.feed_url}",
            f"entries={self.entries_ingested}/{self.entries_seen}",
            f"claims={self.claim_count}",
            f"saved_sources={store.get('sources_saved', 0)}",
            f"saved_claims={store.get('claims_saved', 0)}",
            f"memory_saved={self.memory_saved}",
            f"memory_skipped={self.memory_skipped}",
            f"conflicts={self.conflicts}",
        ]
        if self.dry_run:
            parts.append("dry_run=True")
        if self.errors:
            parts.append(f"errors={len(self.errors)}")
        if self.entry_urls:
            parts.append(f"entries={self.entry_urls[:5]}")
        return "(" + "; ".join(parts) + ")"
