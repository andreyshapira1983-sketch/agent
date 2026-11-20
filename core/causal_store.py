"""Causal observation store: observations survive turns (first rung of the causal ladder)."""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path

from core.causal_lesson import Observation
from core.state_integrity import read_state_jsonl, rewrite_state_jsonl


def observation_fingerprint(observation: Observation) -> str:
    """What makes two observations ONE reason to investigate."""
    key = "|".join(sorted(observation.defect_signals))
    return "cobs_" + hashlib.sha256(key.encode("utf-8")).hexdigest()[:12]


@dataclass(frozen=True)
class ObservationRecord:
    """An observation and its repeat history."""

    fingerprint: str
    defect_signals: tuple[str, ...]
    observed_mismatch: str
    evidence_refs: tuple[str, ...] = ()
    episode_ids: tuple[str, ...] = ()
    occurrences: int = 1
    first_seen: str = ""
    last_seen: str = ""

    def to_dict(self) -> dict:
        return {
            "fingerprint": self.fingerprint,
            "defect_signals": list(self.defect_signals),
            "observed_mismatch": self.observed_mismatch,
            "evidence_refs": list(self.evidence_refs),
            "episode_ids": list(self.episode_ids),
            "occurrences": self.occurrences,
            "first_seen": self.first_seen,
            "last_seen": self.last_seen,
        }

    @classmethod
    def from_dict(cls, row: dict) -> ObservationRecord:
        return cls(
            fingerprint=str(row.get("fingerprint") or ""),
            defect_signals=tuple(str(x) for x in row.get("defect_signals") or ()),
            observed_mismatch=str(row.get("observed_mismatch") or ""),
            evidence_refs=tuple(str(x) for x in row.get("evidence_refs") or ()),
            episode_ids=tuple(str(x) for x in row.get("episode_ids") or ()),
            occurrences=max(1, int(row.get("occurrences") or 1)),
            first_seen=str(row.get("first_seen") or ""),
            last_seen=str(row.get("last_seen") or ""),
        )


class CausalObservationStore:
    """Bottom rung of the ladder on disk; nothing above it."""

    #: How many episodes to remember per record: kept so a later INDEPENDENT case can be chosen for the generalization check; one alone would leave `GeneralizationTest` no choice.
    MAX_EPISODES: int = 12

    def __init__(self, path: Path | str):
        self.path = Path(path)

    def load(self) -> list[ObservationRecord]:
        return [ObservationRecord.from_dict(r) for r in read_state_jsonl(self.path)]

    def record(self, observation: Observation) -> ObservationRecord:
        """Record an observation, merging it with earlier ones by fingerprint."""
        now = datetime.now(timezone.utc).isoformat()
        fingerprint = observation_fingerprint(observation)
        records = self.load()
        out: list[ObservationRecord] = []
        merged: ObservationRecord | None = None
        for rec in records:
            if rec.fingerprint != fingerprint:
                out.append(rec)
                continue
            episodes = tuple(dict.fromkeys(
                (*rec.episode_ids, observation.episode_id)
            ))[-self.MAX_EPISODES:]
            merged = replace(
                rec,
                occurrences=rec.occurrences + 1,
                episode_ids=episodes,
                last_seen=now,
                # Keep the latest wording (the old one described a past run); history stays in the counter.
                observed_mismatch=observation.observed_mismatch,
            )
            out.append(merged)
        if merged is None:
            merged = ObservationRecord(
                fingerprint=fingerprint,
                defect_signals=tuple(observation.defect_signals),
                observed_mismatch=observation.observed_mismatch,
                evidence_refs=tuple(observation.evidence_refs),
                episode_ids=(observation.episode_id,) if observation.episode_id else (),
                occurrences=1,
                first_seen=now,
                last_seen=now,
            )
            out.append(merged)
        rewrite_state_jsonl(self.path, [r.to_dict() for r in out])
        return merged
