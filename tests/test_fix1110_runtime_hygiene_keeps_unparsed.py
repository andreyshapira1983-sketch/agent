"""Pruning rewrites the episode file; a row it cannot parse must survive it."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path

from core.episodic_hygiene import prune_stale_episodes
from core.smart_memory import EpisodeRecord, EpisodicMemoryStore
from core.state_integrity import append_state_jsonl, read_state_jsonl


def _ep(eid: str, *, age_days: float, outcome: str, quality: float, now: datetime) -> EpisodeRecord:
    return EpisodeRecord(
        goal="g", question="q", outcome=outcome, summary="s",  # type: ignore[arg-type]
        answer_quality_score=quality, id=eid,
        created_at=(now - timedelta(days=age_days)).isoformat(),
    )


def test_prune_keeps_a_row_it_cannot_parse(tmp_path: Path):
    store = EpisodicMemoryStore(tmp_path / "ep.jsonl", max_episodes=100)
    now = datetime.now(timezone.utc)
    store.save(_ep("keep", age_days=2, outcome="success", quality=1.0, now=now))
    # Valid JSON, but `used_procedure_ids` is not iterable: from_dict raises.
    unparsed = {"id": "odd", "summary": "hand-edited", "used_procedure_ids": 5}
    append_state_jsonl(store.path, [unparsed])
    store.save(_ep("drop", age_days=60, outcome="failed", quality=0.0, now=now))

    removed = prune_stale_episodes(store, max_age_days=30, now=now)

    assert removed == ["drop"]
    rows = read_state_jsonl(store.path)
    assert {r.get("id") for r in rows} == {"keep", "odd"}
    assert unparsed in rows
