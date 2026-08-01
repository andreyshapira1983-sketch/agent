"""Procedural memory for instruction conflicts: инструкция → конфликт → решение.

A gate that blocks and forgets teaches nothing. The operator's requirement is
that each conflict survives as an episode: what was being asked, which two
sources collided, what the agent refused to do, and — later — how the operator
ruled and what the lesson was.

Three things depend on this record:

* the agent can be shown past episodes before it meets a new trap;
* an exam can check that a stop was *recorded*, not merely narrated;
* accumulated ``instruction → conflict → correct decision`` rows are the raw
  material for a fine-tuning dataset (:meth:`ConflictEpisode.to_training_row`).

Lifecycle
---------

An episode is written ``open`` the moment the gate blocks — before any operator
involvement, so the record cannot be lost by an agent that decides not to
mention the stop. It becomes ``resolved`` only when a human rules on it.

The store is append-only: resolving writes a *new* row with the same ``id``,
and reads collapse rows by id keeping the last. Nothing is ever rewritten in
place, so the history of a disputed ruling stays inspectable.

Design principles
-----------------
* The episode dataclass is pure and frozen; only ``ConflictEpisodeStore``
  touches disk (JSONL under ``data/``, same idiom as the assumption registry).
* Writing an episode must never be able to break the refusal it records —
  callers wrap the write defensively.
"""
from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from core.ids import new_id
from core.instruction_conflict_gate import (
    ConflictFinding,
    InstructionConflictOutcome,
)
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)

#: An episode nobody has ruled on yet.
STATUS_OPEN = "open"
#: An episode closed by an operator ruling.
STATUS_RESOLVED = "resolved"


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def default_path(workspace: Path | str) -> Path:
    """Where episodes live for a given workspace."""
    return Path(workspace) / "data" / "conflict_episodes.jsonl"


# ---------------------------------------------------------------------------
# The episode
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConflictEpisode:
    """One recorded stop: what collided, what was refused, how it ended."""

    id: str
    created_at: str
    context: str                      # what the agent was doing / was asked
    subject: str                      # the axis the two sources fought over
    higher: dict[str, Any]            # the stronger directive, serialised
    lower: dict[str, Any]             # the weaker directive, serialised
    same_level: bool
    priority_verdict: str
    blocked_actions: tuple[str, ...]
    status: str = STATUS_OPEN
    ruling: str = ""                  # the operator's decision, verbatim
    ruled_by: str = ""
    ruled_at: str = ""
    lesson: str = ""                  # what to carry into the next episode

    @property
    def is_open(self) -> bool:
        return self.status == STATUS_OPEN

    def resolve(
        self,
        *,
        ruling: str,
        ruled_by: str,
        lesson: str = "",
        ruled_at: str | None = None,
    ) -> "ConflictEpisode":
        """Return the resolved copy. Never mutates — the store appends it."""
        return replace(
            self,
            status=STATUS_RESOLVED,
            ruling=ruling,
            ruled_by=ruled_by,
            lesson=lesson,
            ruled_at=ruled_at or _now_iso(),
        )

    def to_training_row(self) -> dict[str, Any]:
        """The ``instruction → conflict → decision`` row for a future dataset.

        Only resolved episodes carry a decision; an open one returns the same
        shape with an empty ``decision`` so unresolved rows are filtered, never
        silently presented as answered.
        """
        return {
            "instruction": self.context,
            "conflict": {
                "subject": self.subject,
                "higher": {
                    "level": self.higher.get("source_level"),
                    "source": self.higher.get("source_name"),
                    "demand": self.higher.get("demand"),
                    "quote": self.higher.get("quote"),
                },
                "lower": {
                    "level": self.lower.get("source_level"),
                    "source": self.lower.get("source_name"),
                    "demand": self.lower.get("demand"),
                    "quote": self.lower.get("quote"),
                },
                "priority_verdict": self.priority_verdict,
            },
            "correct_action": "stop; change nothing; report both sources",
            "decision": self.ruling,
            "lesson": self.lesson,
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "created_at": self.created_at,
            "context": self.context,
            "subject": self.subject,
            "higher": dict(self.higher),
            "lower": dict(self.lower),
            "same_level": self.same_level,
            "priority_verdict": self.priority_verdict,
            "blocked_actions": list(self.blocked_actions),
            "status": self.status,
            "ruling": self.ruling,
            "ruled_by": self.ruled_by,
            "ruled_at": self.ruled_at,
            "lesson": self.lesson,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "ConflictEpisode":
        return cls(
            id=str(row["id"]),
            created_at=str(row.get("created_at") or ""),
            context=str(row.get("context") or ""),
            subject=str(row.get("subject") or ""),
            higher=dict(row.get("higher") or {}),
            lower=dict(row.get("lower") or {}),
            same_level=bool(row.get("same_level")),
            priority_verdict=str(row.get("priority_verdict") or ""),
            blocked_actions=tuple(row.get("blocked_actions") or ()),
            status=str(row.get("status") or STATUS_OPEN),
            ruling=str(row.get("ruling") or ""),
            ruled_by=str(row.get("ruled_by") or ""),
            ruled_at=str(row.get("ruled_at") or ""),
            lesson=str(row.get("lesson") or ""),
        )


def episode_from_finding(
    finding: ConflictFinding,
    *,
    blocked_actions: Iterable[str],
    context: str = "",
    now_iso: str | None = None,
) -> ConflictEpisode:
    return ConflictEpisode(
        id=new_id("conflict"),
        created_at=now_iso or _now_iso(),
        context=context,
        subject=finding.subject,
        higher=finding.higher.to_dict(),
        lower=finding.lower.to_dict(),
        same_level=finding.same_level,
        priority_verdict=finding.priority_verdict(),
        blocked_actions=tuple(blocked_actions),
    )


def episodes_from_outcome(
    outcome: InstructionConflictOutcome,
    *,
    context: str = "",
    now_iso: str | None = None,
) -> tuple[ConflictEpisode, ...]:
    """One episode per conflicting subject. Empty when the gate let work pass."""
    if not outcome.is_blocked:
        return ()
    return tuple(
        episode_from_finding(
            finding,
            blocked_actions=outcome.forbidden_actions,
            context=context,
            now_iso=now_iso,
        )
        for finding in outcome.findings
    )


# ---------------------------------------------------------------------------
# The store
# ---------------------------------------------------------------------------

class ConflictEpisodeStore:
    """Append-only JSONL store. Rows with a repeated ``id`` supersede earlier ones."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- writes ----------

    def save(self, episode: ConflictEpisode) -> None:
        self.save_many([episode])

    def save_many(self, episodes: Iterable[ConflictEpisode]) -> int:
        payloads = [episode.to_dict() for episode in episodes]
        if not payloads:
            return 0
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, payloads)
        return len(payloads)

    def resolve(
        self,
        episode_id: str,
        *,
        ruling: str,
        ruled_by: str,
        lesson: str = "",
        ruled_at: str | None = None,
    ) -> ConflictEpisode | None:
        """Append the resolved version of an episode. None if the id is unknown."""
        current = self.get(episode_id)
        if current is None:
            return None
        resolved = current.resolve(
            ruling=ruling, ruled_by=ruled_by, lesson=lesson, ruled_at=ruled_at
        )
        self.save(resolved)
        return resolved

    # ---------- reads ----------

    def load_all(self) -> tuple[ConflictEpisode, ...]:
        """Every episode, oldest first, collapsed by id keeping the last row."""
        if not self.path.exists():
            return ()
        by_id: dict[str, ConflictEpisode] = {}
        for row in read_state_jsonl_unlocked(self.path):
            try:
                episode = ConflictEpisode.from_dict(row)
            except Exception:  # noqa: BLE001 — a corrupt row must not hide the rest
                continue
            by_id[episode.id] = episode
        return tuple(by_id.values())

    def get(self, episode_id: str) -> ConflictEpisode | None:
        for episode in self.load_all():
            if episode.id == episode_id:
                return episode
        return None

    def load_open(self) -> tuple[ConflictEpisode, ...]:
        return tuple(e for e in self.load_all() if e.is_open)

    def load_recent(self, n: int = 20) -> tuple[ConflictEpisode, ...]:
        """The last *n* episodes, most recent first."""
        every = self.load_all()
        return tuple(reversed(every[-max(1, n):])) if every else ()

    def training_rows(self, *, resolved_only: bool = True) -> tuple[dict[str, Any], ...]:
        """Dataset rows. Unresolved episodes are excluded by default."""
        return tuple(
            episode.to_training_row()
            for episode in self.load_all()
            if not resolved_only or episode.status == STATUS_RESOLVED
        )
