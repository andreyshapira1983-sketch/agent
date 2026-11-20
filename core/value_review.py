"""TD-032: human value-review verdicts for self-build / self-apply outcomes.

A technical lane outcome (``approved`` / ``committed_local`` / ``rolled_back``) proves a change was accepted/applied, not that it was valuable; this module captures a **human** verdict as a separate append-only signal.
Append-only ledger ``data/value_reviews.jsonl`` (git-ignored); the effective verdict for an item is the latest valid review; the approval inbox is never mutated; registry scoring lives in the CLI (TD-033); notes are secret-redacted and truncated before persisting.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.redaction import redact_text
from core.state_integrity import append_state_jsonl, read_state_jsonl

ValueVerdict = Literal[
    "accepted",
    "rejected_low_value",
    "rejected_misleading_summary",
    "rejected_risky",
    "rejected_wrong_target",
]

# The complete, closed set of verdicts; anything else is rejected on write.
VALID_VERDICTS: frozenset[str] = frozenset(
    {
        "accepted",
        "rejected_low_value",
        "rejected_misleading_summary",
        "rejected_risky",
        "rejected_wrong_target",
    }
)

DEFAULT_LEDGER_PATH = Path("data") / "value_reviews.jsonl"

# Notes are operator free text; kept short so the ledger is no dumping ground and no long secret survives redaction.
MAX_NOTE_CHARS = 500
_TRUNCATION_MARKER = "...[truncated]"


class InvalidVerdictError(ValueError):
    """Raised when a verdict is outside :data:`VALID_VERDICTS`."""


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sanitize_note(note: str | None) -> str:
    """Redact secrets, then hard-truncate (redaction first, so a secret is never sliced in half)."""
    if not note:
        return ""
    redacted, _findings = redact_text(str(note))
    redacted = redacted.strip()
    if len(redacted) > MAX_NOTE_CHARS:
        keep = MAX_NOTE_CHARS - len(_TRUNCATION_MARKER)
        redacted = redacted[: max(0, keep)] + _TRUNCATION_MARKER
    return redacted


@dataclass(frozen=True)
class ValueReview:
    """One human verdict about the value of an applied self-build proposal."""

    item_id: str
    verdict: ValueVerdict
    reviewer: str = "human"
    note: str = ""
    commit_hash: str | None = None
    created_at: str = field(default_factory=_now_iso)

    def to_dict(self) -> dict:
        return {
            "item_id": self.item_id,
            "verdict": self.verdict,
            "reviewer": self.reviewer,
            "note": self.note,
            "commit_hash": self.commit_hash,
            "created_at": self.created_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> ValueReview:
        item_id = str(data.get("item_id") or "").strip()
        verdict = str(data.get("verdict") or "").strip()
        if not item_id or verdict not in VALID_VERDICTS:
            raise ValueError("invalid value-review row")
        commit_hash = data.get("commit_hash")
        return cls(
            item_id=item_id,
            verdict=verdict,  # type: ignore[arg-type]
            reviewer=str(data.get("reviewer") or "human"),
            note=str(data.get("note") or ""),
            commit_hash=(str(commit_hash) if commit_hash else None),
            created_at=str(data.get("created_at") or _now_iso()),
        )


class ValueReviewLog:
    """Append-only, tolerant-loading ledger of :class:`ValueReview` rows."""

    def __init__(self, path: Path | str | None = None):
        self.path = Path(path) if path is not None else None

    @classmethod
    def for_workspace(cls, workspace: Path | str) -> ValueReviewLog:
        return cls(Path(workspace) / DEFAULT_LEDGER_PATH)

    def list(self) -> list[ValueReview]:
        """All valid reviews in write order. Missing/empty/corrupt file → []."""
        if self.path is None or not self.path.exists():
            return []
        reviews: list[ValueReview] = []
        for raw in read_state_jsonl(self.path):
            try:
                reviews.append(ValueReview.from_dict(raw))
            except ValueError:
                continue  # skip malformed/legacy rows, never raise
        return reviews

    def effective_by_item_id(self) -> dict[str, ValueReview]:
        """Latest valid review per ``item_id`` (write order → last wins)."""
        effective: dict[str, ValueReview] = {}
        for review in self.list():
            effective[review.item_id] = review
        return effective

    def append(
        self,
        item_id: str,
        verdict: str,
        *,
        reviewer: str = "human",
        note: str = "",
        commit_hash: str | None = None,
    ) -> ValueReview:
        """Validate and persist one review; raises :class:`InvalidVerdictError` (writing nothing) for an unknown ``verdict``. The note is redacted and truncated before it touches disk."""
        if verdict not in VALID_VERDICTS:
            raise InvalidVerdictError(
                f"invalid verdict {verdict!r}; expected one of "
                f"{sorted(VALID_VERDICTS)}"
            )
        if self.path is None:
            raise ValueError("ValueReviewLog has no path to write to")
        review = ValueReview(
            item_id=str(item_id),
            verdict=verdict,  # type: ignore[arg-type]
            reviewer=str(reviewer or "human"),
            note=_sanitize_note(note),
            commit_hash=(str(commit_hash) if commit_hash else None),
        )
        append_state_jsonl(self.path, [review.to_dict()])
        return review
