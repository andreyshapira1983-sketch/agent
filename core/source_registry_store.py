"""Persistent store for SourceRegistry."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import replace
from pathlib import Path

from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)


class SourceRegistryStore:
    """JSONL-backed source/claim catalog with duplicate suppression."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save_registry(self, registry: SourceRegistry) -> dict[str, int]:
        """Persist a whole registry with a SINGLE file scan and one append.

        The naive version called ``save_source``/``save_claim`` per record,
        and each of those re-read (and re-encoded) the ENTIRE JSONL file to
        check for duplicates. Ingesting M records into a store of N records
        was therefore O(M*N) full-file reads — which, once the registry grew
        into the thousands of claims, was slow enough to appear to hang.

        Here we read the existing catalog exactly once under a single lock,
        build in-memory dedup sets, decide which incoming records are new,
        and append them all in one batch. Dedup semantics are preserved
        bit-for-bit with the old per-record path:

          * a source is "new" iff its id is not already present;
          * a claim is "new" iff its ``_claim_key`` is not already present,
            where — mirroring ``_load_claims_unlocked`` — an existing claim
            only counts if its ``source_id`` is among the known sources
            (existing sources plus every source in this registry, since the
            old code appended all sources before checking any claim);
          * duplicates WITHIN the incoming registry are collapsed the same
            way the sequential appends collapsed them;
          * a known claim whose status changed is appended again (the last
            row wins on load), except that `suspect` is never lowered
            (проверка 17.11, средняя).
        """
        with state_file_lock(self.path):
            existing_source_ids: set[str] = set()
            # (claim_key, source_id, status) — status kept so a change of it
            # is stored (проверка 17.11, средняя).
            file_claim_keys: list[tuple[str, str, str]] = []
            for kind, payload in self._iter_records_unlocked():
                if kind == "source":
                    try:
                        existing_source_ids.add(SourceRecord.from_dict(payload).id)
                    except (TypeError, ValueError):
                        continue
                elif kind == "claim":
                    try:
                        claim = ClaimRecord.from_dict(payload)
                    except (TypeError, ValueError):
                        continue
                    file_claim_keys.append((_claim_key(claim), claim.source_id, claim.status))

            new_rows: list[dict] = []
            source_saved = 0
            known_source_ids = set(existing_source_ids)
            for source in registry.sources:
                if source.id in known_source_ids:
                    continue
                known_source_ids.add(source.id)
                new_rows.append({"kind": "source", "payload": source.to_dict()})
                source_saved += 1

            # Sources visible when filtering claims = everything now in the
            # file (existing sources + all incoming sources are appended
            # before any claim in the legacy path).
            combined_source_ids = existing_source_ids | {s.id for s in registry.sources}
            # key -> stored status, the last row winning with `suspect`
            # sticky, as in _load_claims_unlocked.
            stored_status: dict[str, str] = {}
            for key, source_id, status in file_claim_keys:
                if source_id in combined_source_ids:
                    stored_status[key] = _merged_status(stored_status.get(key), status)

            claim_saved = 0
            for claim in registry.claims:
                key = _claim_key(claim)
                if key in stored_status and _merged_status(stored_status[key], claim.status) == stored_status[key]:
                    continue
                new_rows.append({"kind": "claim", "payload": claim.to_dict()})
                claim_saved += 1
                # Only a claim whose source is known becomes visible to later
                # dedup checks — matching _load_claims_unlocked's filter.
                if claim.source_id in combined_source_ids:
                    stored_status[key] = _merged_status(stored_status.get(key), claim.status)

            if new_rows:
                append_state_jsonl_unlocked(self.path, new_rows)

        return {
            "sources_saved": source_saved,
            "claims_saved": claim_saved,
            "sources_total": len(registry.sources),
            "claims_total": len(registry.claims),
        }

    def load_sources(self) -> list[SourceRecord]:
        with state_file_lock(self.path):
            return self._load_sources_unlocked()

    def _load_sources_unlocked(self) -> list[SourceRecord]:
        sources: dict[str, SourceRecord] = {}
        for kind, payload in self._iter_records_unlocked():
            if kind != "source":
                continue
            try:
                source = SourceRecord.from_dict(payload)
            except (TypeError, ValueError):
                continue
            sources[source.id] = source
        return list(sources.values())

    def load_claims(self) -> list[ClaimRecord]:
        with state_file_lock(self.path):
            return self._load_claims_unlocked()

    def _load_claims_unlocked(self) -> list[ClaimRecord]:
        claims: dict[str, ClaimRecord] = {}
        sources = {source.id for source in self._load_sources_unlocked()}
        for kind, payload in self._iter_records_unlocked():
            if kind != "claim":
                continue
            try:
                claim = ClaimRecord.from_dict(payload)
            except (TypeError, ValueError):
                continue
            if claim.source_id in sources:
                key = _claim_key(claim)
                prev = claims.get(key)
                if prev is not None and prev.status == "suspect" and claim.status != "suspect":
                    claim = replace(claim, status="suspect")
                claims[key] = claim
        return list(claims.values())

    def load_registry(self) -> SourceRegistry:
        return SourceRegistry.from_records(
            sources=self.load_sources(),
            claims=self.load_claims(),
        )

    def get_source(self, source_id: str) -> SourceRecord | None:
        for source in self.load_sources():
            if source.id == source_id:
                return source
        return None

    def count(self) -> dict[str, int]:
        return {
            "sources": len(self.load_sources()),
            "claims": len(self.load_claims()),
        }

    def delete_all(self) -> dict[str, int]:
        counts = self.count()
        with state_file_lock(self.path):
            if self.path.exists():
                self.path.unlink()
        return counts

    def _iter_records_unlocked(self) -> Iterable[tuple[str, dict]]:
        if not self.path.exists():
            return
        for row in read_state_jsonl_unlocked(self.path):
            kind = row.get("kind")
            payload = row.get("payload")
            if isinstance(kind, str) and isinstance(payload, dict):
                yield kind, payload


#: Status strength: a later save may only RAISE it. A re-ingest that sees the
#: claim with fewer witnesses (`extracted`) no longer erases a `verified`, and
#: each claim can change at most four times, so the file cannot grow per save
#: (проверка 17.11, вечер; `suspect` stays sticky as before).
_STATUS_RANK = {"extracted": 0, "unverified": 1, "verified": 2, "conflicted": 3, "suspect": 4}


def _merged_status(stored: str | None, incoming: str) -> str:
    """Status after a save: the stronger of the stored and the incoming one."""
    if stored is None:
        return incoming
    return max(stored, incoming, key=lambda s: _STATUS_RANK.get(s, 4))


def _claim_key(claim: ClaimRecord) -> str:
    return "\x1f".join([
        claim.source_id,
        claim.locator,
        " ".join(claim.text.casefold().split()),
    ])
