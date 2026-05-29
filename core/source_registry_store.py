"""Persistent store for SourceRegistry.

The source catalog is separate from long-term memory. Memory stores verified
knowledge the agent may reuse in prompts. SourceRegistryStore stores the
audit/catalog trail: which sources were seen and which claims were extracted.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

from core.source_registry import ClaimRecord, SourceRecord, SourceRegistry


class SourceRegistryStore:
    """JSONL-backed source/claim catalog with duplicate suppression."""

    def __init__(self, path: Path | str):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def save_source(self, source: SourceRecord) -> bool:
        if self.get_source(source.id) is not None:
            return False
        self._append("source", source.to_dict())
        return True

    def save_claim(self, claim: ClaimRecord) -> bool:
        if self._has_claim_key(_claim_key(claim)):
            return False
        self._append("claim", claim.to_dict())
        return True

    def save_registry(self, registry: SourceRegistry) -> dict[str, int]:
        source_saved = 0
        claim_saved = 0
        for source in registry.sources:
            if self.save_source(source):
                source_saved += 1
        for claim in registry.claims:
            if self.save_claim(claim):
                claim_saved += 1
        return {
            "sources_saved": source_saved,
            "claims_saved": claim_saved,
            "sources_total": len(registry.sources),
            "claims_total": len(registry.claims),
        }

    def load_sources(self) -> list[SourceRecord]:
        sources: dict[str, SourceRecord] = {}
        for kind, payload in self._iter_records():
            if kind != "source":
                continue
            try:
                source = SourceRecord.from_dict(payload)
            except (TypeError, ValueError):
                continue
            sources[source.id] = source
        return list(sources.values())

    def load_claims(self) -> list[ClaimRecord]:
        claims: dict[str, ClaimRecord] = {}
        sources = {source.id for source in self.load_sources()}
        for kind, payload in self._iter_records():
            if kind != "claim":
                continue
            try:
                claim = ClaimRecord.from_dict(payload)
            except (TypeError, ValueError):
                continue
            if claim.source_id in sources:
                claims[_claim_key(claim)] = claim
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
        if self.path.exists():
            self.path.unlink()
        return counts

    def _append(self, kind: str, payload: dict) -> None:
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps({"kind": kind, "payload": payload}, ensure_ascii=False) + "\n")

    def _iter_records(self) -> Iterable[tuple[str, dict]]:
        if not self.path.exists():
            return
        with self.path.open(encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    row = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if not isinstance(row, dict):
                    continue
                kind = row.get("kind")
                payload = row.get("payload")
                if isinstance(kind, str) and isinstance(payload, dict):
                    yield kind, payload

    def _has_claim_key(self, key: str) -> bool:
        for claim in self.load_claims():
            if _claim_key(claim) == key:
                return True
        return False


def _claim_key(claim: ClaimRecord) -> str:
    return "\x1f".join([
        claim.source_id,
        claim.locator,
        " ".join(claim.text.casefold().split()),
    ])

