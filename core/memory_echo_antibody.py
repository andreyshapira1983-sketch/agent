"""Memory Echo Antibody (A1) — refuse agent-auto memory that *echoes* itself.

The agent's failure mode this guards against is the "echo chamber": an
autonomous loop that keeps re-writing the same lesson / observation /
"insight" to persistent memory under slightly different wording, cycle after
cycle. The existing `core.hygiene.find_duplicate` already refuses an exact or
near-duplicate of what is *already on disk*, but it has no notion of *time* and
no notion of *who* wrote it. This antibody adds exactly those two missing
properties and nothing else:

    1. **Time window.** Only writes made in the recent past (default 24h)
       count as an echo. A genuine new observation a week later is fine.
    2. **Source scope.** Only the agent's own `agent-auto` writes are guarded.
       The human operator (`user-explicit`) is never limited — the point is to
       stop the *agent* from talking to itself, not the person.

HARD BOUNDARIES (by design, do not relax):
    * It NEVER deletes memory.
    * It NEVER writes new memory.
    * It NEVER calls an LLM.
    * It draws NO conclusions about meaning, truth, or value.
    * It only ever returns ``allow`` / ``reject`` plus a human-readable reason.

The detector (`detect_memory_echo`) is a pure function: same inputs → same
outputs, no I/O. Persistence of the rolling write-log lives in the small
append-only `MemoryWriteRegistry` (data/memory_writes.jsonl), which is the only
part that touches disk and is kept deliberately separate from the decision.
"""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Literal, Optional, Sequence

# Reuse the proven Jaccard+containment scorer so the echo guard stays in lock
# step with the on-disk dedup gate (`core.hygiene`). Importing the internal
# helper avoids re-implementing — and silently drifting from — the similarity
# definition the rest of the system already trusts.
from core.hygiene import _similarity as _text_similarity

# The single write source this antibody guards. Everything else — most
# importantly `user-explicit` — passes straight through untouched.
GUARDED_SOURCE = "agent-auto"

# Mirror of `core.hygiene.DEFAULT_DEDUP_THRESHOLD`; kept as its own name so the
# echo threshold can be tuned independently of the on-disk dedup threshold if
# they ever need to diverge.
DEFAULT_ECHO_THRESHOLD = 0.85

# How far back a prior write still counts as a potential echo.
DEFAULT_WINDOW_HOURS = 24.0

ECHO_REASON = "memory_echo_suspected"

_WS_RE = re.compile(r"\s+")


def _normalise(text: str) -> str:
    """Case-insensitive, whitespace-collapsed key (matches hygiene)."""
    return _WS_RE.sub(" ", (text or "").strip().lower())


def content_hash(text: str) -> str:
    """Stable SHA-256 of the *normalised* content (exact-repeat fingerprint)."""
    return hashlib.sha256(_normalise(text).encode("utf-8")).hexdigest()


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _parse_ts(raw: str) -> Optional[datetime]:
    """Best-effort ISO-8601 → aware datetime; None when unparseable."""
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


# ============================================================
# Write-log record
# ============================================================


@dataclass(frozen=True)
class MemoryWriteEvent:
    """One agent-auto write as recorded in the rolling log.

    The normalised ``content`` is stored so the *semantic* echo check can run
    later; ``content_hash`` is the fast exact-repeat fingerprint.
    """

    content: str
    content_hash: str
    tags: tuple[str, ...]
    record_type: str
    source: str
    cycle_id: str
    ts: str  # ISO-8601 UTC

    def to_dict(self) -> dict:
        return {
            "content": self.content,
            "content_hash": self.content_hash,
            "tags": list(self.tags),
            "record_type": self.record_type,
            "source": self.source,
            "cycle_id": self.cycle_id,
            "ts": self.ts,
        }

    @classmethod
    def from_dict(cls, data: dict) -> "MemoryWriteEvent":
        return cls(
            content=str(data.get("content", "")),
            content_hash=str(data.get("content_hash", "")),
            tags=tuple(data.get("tags", []) or []),
            record_type=str(data.get("record_type", "")),
            source=str(data.get("source", "")),
            cycle_id=str(data.get("cycle_id", "")),
            ts=str(data.get("ts", "")),
        )


def make_event(
    content: str,
    *,
    tags: Iterable[str] = (),
    record_type: str = "semantic",
    source: str = GUARDED_SOURCE,
    cycle_id: str = "",
    now: Optional[datetime] = None,
) -> MemoryWriteEvent:
    """Build a normalised :class:`MemoryWriteEvent` for the candidate write."""
    ts = (now or _utcnow()).astimezone(timezone.utc).isoformat()
    return MemoryWriteEvent(
        content=_normalise(content),
        content_hash=content_hash(content),
        tags=tuple(tags),
        record_type=record_type,
        source=source,
        cycle_id=cycle_id,
        ts=ts,
    )


# ============================================================
# Pure detector
# ============================================================


@dataclass(frozen=True)
class MemoryEchoOutcome:
    """Verdict of the echo detector. ``allow`` or ``reject`` only."""

    decision: Literal["allow", "reject"]
    reason: str
    echo_within_window: bool
    matched_hash: Optional[str]
    similarity: float

    @property
    def is_reject(self) -> bool:
        return self.decision == "reject"

    def to_dict(self) -> dict:
        return {
            "decision": self.decision,
            "reason": self.reason,
            "echo_within_window": self.echo_within_window,
            "matched_hash": self.matched_hash,
            "similarity": round(self.similarity, 4),
        }


def detect_memory_echo(
    *,
    candidate_content: str,
    candidate_source: str = GUARDED_SOURCE,
    recent_writes: Iterable[MemoryWriteEvent] = (),
    echo_threshold: float = DEFAULT_ECHO_THRESHOLD,
) -> MemoryEchoOutcome:
    """Decide whether ``candidate_content`` echoes a recent agent-auto write.

    ``recent_writes`` is expected to be **already time-windowed** (the registry
    owns the clock); this keeps the detector a pure content+source function.

    Returns ``allow`` for anything that is not a guarded agent-auto write, for
    empty content (other policy gates own that), or when nothing recent is
    close enough. Returns ``reject`` with ``reason=memory_echo_suspected`` on an
    exact repeat or a similarity at/above ``echo_threshold``.
    """
    source = (candidate_source or "").strip().lower()
    if source != GUARDED_SOURCE:
        return MemoryEchoOutcome(
            decision="allow",
            reason=f"source '{candidate_source}' is not guarded (echo guard is agent-auto only)",
            echo_within_window=False,
            matched_hash=None,
            similarity=0.0,
        )

    text = (candidate_content or "").strip()
    if not text:
        # Empty / blank content is handled by the write policy's own gates;
        # the echo guard stays silent rather than double-reporting.
        return MemoryEchoOutcome(
            decision="allow",
            reason="empty candidate (handled by other gates)",
            echo_within_window=False,
            matched_hash=None,
            similarity=0.0,
        )

    cand_hash = content_hash(text)
    best_sim = 0.0
    best_hash: Optional[str] = None

    for event in recent_writes:
        if event.content_hash and event.content_hash == cand_hash:
            return MemoryEchoOutcome(
                decision="reject",
                reason=f"{ECHO_REASON}: exact repeat of a recent agent-auto write",
                echo_within_window=True,
                matched_hash=event.content_hash,
                similarity=1.0,
            )
        sim = _text_similarity(text, event.content)
        if sim > best_sim:
            best_sim = sim
            best_hash = event.content_hash or None

    if best_sim >= echo_threshold:
        return MemoryEchoOutcome(
            decision="reject",
            reason=f"{ECHO_REASON}: semantically close (similarity={best_sim:.2f} >= {echo_threshold}) to a recent agent-auto write",
            echo_within_window=True,
            matched_hash=best_hash,
            similarity=best_sim,
        )

    return MemoryEchoOutcome(
        decision="allow",
        reason="no echo within window",
        echo_within_window=False,
        matched_hash=best_hash,
        similarity=best_sim,
    )


# ============================================================
# Append-only rolling write-log (the only part that touches disk)
# ============================================================

DEFAULT_REGISTRY_PATH = Path("data") / "memory_writes.jsonl"


class MemoryWriteRegistry:
    """Append-only JSONL log of agent-auto memory writes.

    Deliberately dumb: it records events and serves a time-windowed slice of
    recent ones. It makes no decisions — that is the detector's job.
    """

    def __init__(self, path: Optional[Path | str] = None) -> None:
        self.path = Path(path) if path is not None else DEFAULT_REGISTRY_PATH

    def append(self, event: MemoryWriteEvent) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(event.to_dict(), ensure_ascii=False) + "\n")

    def load(self) -> list[MemoryWriteEvent]:
        if not self.path.exists():
            return []
        events: list[MemoryWriteEvent] = []
        with self.path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    events.append(MemoryWriteEvent.from_dict(json.loads(line)))
                except (ValueError, TypeError):
                    # A single corrupt line must never sink the run.
                    continue
        return events

    def recent(
        self,
        *,
        window_hours: float = DEFAULT_WINDOW_HOURS,
        now: Optional[datetime] = None,
        source: str = GUARDED_SOURCE,
    ) -> list[MemoryWriteEvent]:
        """Events newer than ``window_hours`` ago, restricted to ``source``.

        ``window_hours <= 0`` means *no time limit* (consistent with the
        codebase '0 = off/unlimited' convention); events with an unparseable
        timestamp are excluded from a windowed query (fail closed: an event we
        can't date can't be proven recent).
        """
        reference = (now or _utcnow()).astimezone(timezone.utc)
        wanted_source = (source or "").strip().lower() if source else ""
        out: list[MemoryWriteEvent] = []
        for event in self.load():
            if wanted_source and (event.source or "").strip().lower() != wanted_source:
                continue
            if window_hours and window_hours > 0:
                ts = _parse_ts(event.ts)
                if ts is None:
                    continue
                age_hours = (reference - ts).total_seconds() / 3600.0
                if age_hours > window_hours:
                    continue
            out.append(event)
        return out


def recent_within_window(
    events: Sequence[MemoryWriteEvent],
    *,
    window_hours: float = DEFAULT_WINDOW_HOURS,
    now: Optional[datetime] = None,
    source: str = GUARDED_SOURCE,
) -> list[MemoryWriteEvent]:
    """Pure window filter over an in-memory sequence (no disk).

    Mirrors :meth:`MemoryWriteRegistry.recent` so callers that already hold the
    events (e.g. tests, or a caller that loaded once) can window them without a
    second disk read.
    """
    reference = (now or _utcnow()).astimezone(timezone.utc)
    wanted_source = (source or "").strip().lower() if source else ""
    out: list[MemoryWriteEvent] = []
    for event in events:
        if wanted_source and (event.source or "").strip().lower() != wanted_source:
            continue
        if window_hours and window_hours > 0:
            ts = _parse_ts(event.ts)
            if ts is None:
                continue
            age_hours = (reference - ts).total_seconds() / 3600.0
            if age_hours > window_hours:
                continue
        out.append(event)
    return out
