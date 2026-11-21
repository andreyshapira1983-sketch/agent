"""Tests for the Memory Echo Antibody (A1).

Covers the operator-specified cases:
    * exact duplicate  -> reject
    * semantic duplicate -> reject
    * fresh new write  -> allow
    * user-explicit bypass -> allow (never guarded)
    * time window: an older-than-window write is not an echo -> allow

Plus the hard-boundary guarantees (pure, deterministic, no I/O in the
detector) and the rolling-log registry behaviour.
"""

from datetime import datetime, timedelta, timezone

from core.memory_echo_antibody import (
    DEFAULT_ECHO_THRESHOLD,
    ECHO_REASON,
    GUARDED_SOURCE,
    MemoryWriteEvent,
    MemoryWriteRegistry,
    detect_memory_echo,
    make_event,
    recent_within_window,
)


def _event(content: str, *, source: str = GUARDED_SOURCE, **kw) -> MemoryWriteEvent:
    return make_event(content, source=source, **kw)


# ---------------------------------------------------------------------------
# The five operator-specified cases
# ---------------------------------------------------------------------------


def test_semantic_duplicate_is_rejected():
    prior = _event("agent keeps retrying the failed deploy step")
    # Same fact, words reordered — not byte-identical, but the proven
    # Jaccard scorer recognises the identical token set as a near-duplicate.
    outcome = detect_memory_echo(
        candidate_content="the failed deploy step keeps retrying agent",
        recent_writes=[prior],
    )
    assert outcome.decision == "reject"
    assert ECHO_REASON in outcome.reason
    assert outcome.echo_within_window is True
    assert outcome.similarity >= DEFAULT_ECHO_THRESHOLD


def test_user_explicit_is_never_guarded():
    prior = _event("Remember to water the office plants on Friday.")
    # Byte-identical content, but the candidate is a HUMAN write -> bypass.
    outcome = detect_memory_echo(
        candidate_content="Remember to water the office plants on Friday.",
        candidate_source="user-explicit",
        recent_writes=[prior],
    )
    assert outcome.decision == "allow"
    assert "not guarded" in outcome.reason


def test_write_older_than_window_is_not_an_echo():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    old = make_event(
        "The deploy script fails when DB_URL is unset.",
        now=now - timedelta(hours=30),
    )
    # The registry/window filter removes the stale event; nothing recent left.
    in_window = recent_within_window([old], window_hours=24.0, now=now)
    assert in_window == []
    outcome = detect_memory_echo(
        candidate_content="The deploy script fails when DB_URL is unset.",
        recent_writes=in_window,
    )
    assert outcome.decision == "allow"


# ---------------------------------------------------------------------------
# Window filter
# ---------------------------------------------------------------------------


def test_window_keeps_recent_drops_old():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    recent = make_event("recent note one two three", now=now - timedelta(hours=2))
    stale = make_event("stale note four five six", now=now - timedelta(hours=48))
    kept = recent_within_window([recent, stale], window_hours=24.0, now=now)
    assert kept == [recent]


def test_window_excludes_other_sources():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    human = make_event("human note", source="user-explicit", now=now)
    auto = make_event("auto note", source=GUARDED_SOURCE, now=now)
    kept = recent_within_window([human, auto], window_hours=24.0, now=now)
    assert kept == [auto]


def test_unparseable_timestamp_is_excluded_when_windowed():
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    broken = MemoryWriteEvent(
        content="x", content_hash="h", tags=(), record_type="semantic",
        source=GUARDED_SOURCE, cycle_id="c", ts="not-a-date",
    )
    assert recent_within_window([broken], window_hours=24.0, now=now) == []
    # but unlimited window keeps it
    assert recent_within_window([broken], window_hours=0, now=now) == [broken]


# ---------------------------------------------------------------------------
# Hard boundaries / purity
# ---------------------------------------------------------------------------


def test_empty_candidate_is_allowed_silently():
    outcome = detect_memory_echo(candidate_content="   ", recent_writes=[_event("x y z")])
    assert outcome.decision == "allow"
    assert "empty" in outcome.reason.lower()


def test_outcome_serialises():
    outcome = detect_memory_echo(
        candidate_content="dup one two", recent_writes=[_event("dup one two")]
    )
    d = outcome.to_dict()
    assert d["decision"] == "reject"
    assert d["echo_within_window"] is True
    assert isinstance(d["similarity"], float)
    assert outcome.is_reject is True


# ---------------------------------------------------------------------------
# Registry round-trip (the only disk-touching part)
# ---------------------------------------------------------------------------


def test_registry_append_and_recent(tmp_path):
    reg = MemoryWriteRegistry(tmp_path / "memory_writes.jsonl")
    now = datetime(2026, 6, 6, 12, 0, tzinfo=timezone.utc)
    reg.append(make_event("first auto note", now=now - timedelta(hours=1)))
    reg.append(make_event("second auto note", now=now - timedelta(hours=40)))
    reg.append(make_event("human note", source="user-explicit", now=now))

    recent = reg.recent(window_hours=24.0, now=now)
    contents = [e.content for e in recent]
    assert "first auto note" in contents
    assert "second auto note" not in contents  # too old
    assert "human note" not in contents  # wrong source


def test_registry_skips_corrupt_lines(tmp_path):
    path = tmp_path / "memory_writes.jsonl"
    path.write_text(
        '{"content": "good", "content_hash": "h", "source": "agent-auto", "ts": ""}\n'
        "this is not json\n",
        encoding="utf-8",
    )
    reg = MemoryWriteRegistry(path)
    loaded = reg.load()
    assert len(loaded) == 1
    assert loaded[0].content == "good"
