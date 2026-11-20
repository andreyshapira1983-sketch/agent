"""Explicit planning assumptions: heuristic (regex, no LLM) extraction from questions and tool plans, in-memory registry per run, durable JSONL store.

Categories: file_encoding, file_format, language, python_version, tool_availability, network_access, workspace_permission, user_intent, scope, general.
Assumption errors must never abort a run answer; loop.py wraps all registry calls defensively.
"""
from __future__ import annotations

import html
import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, field_validator

from core.ids import new_id
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)

logger = logging.getLogger(__name__)


AssumptionCategory = Literal[
    "file_encoding",
    "file_format",
    "language",
    "python_version",
    "tool_availability",
    "network_access",
    "workspace_permission",
    "user_intent",
    "scope",
    "general",
]


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: Hours after which an unchanged assumption is written again (see `save_many`).
_REPEAT_AFTER = timedelta(hours=24)


def _recent_keys(rows: list[dict]) -> dict[tuple, datetime]:
    """(category, text, verified) -> when last written."""
    seen: dict[tuple, datetime] = {}
    for row in rows:
        try:
            at = datetime.fromisoformat(str(row.get("created_at")))
        except ValueError:
            continue
        at = at if at.tzinfo else at.replace(tzinfo=timezone.utc)
        key = (row.get("category"), str(row.get("text") or "").strip(), row.get("verified"))
        if at > seen.get(key, datetime.min.replace(tzinfo=timezone.utc)):
            seen[key] = at
    return seen



class Assumption(BaseModel):
    """A single explicit assumption made during one agent run."""

    id: str = Field(default_factory=lambda: new_id("asmp"))
    run_id: str = ""  # trace_id of the owning run
    text: str  # human-readable statement
    category: AssumptionCategory = "general"
    confidence: float = 0.80  # 0.0-1.0
    source: Literal[
        "heuristic", "planner", "question", "kernel", "profile"
    ] = "heuristic"
    verified: bool | None = None  # None = not yet checked
    created_at: datetime = Field(default_factory=_now)

    @field_validator("confidence")
    @classmethod
    def _confidence_in_range(cls, v: float) -> float:
        if not (0.0 <= v <= 1.0):
            raise ValueError(f"confidence must be in [0.0, 1.0], got {v}")
        return v

    @field_validator("text")
    @classmethod
    def _text_not_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("assumption text must not be empty")
        if len(v) > 500:
            raise ValueError(f"assumption text too long ({len(v)} chars, max 500)")
        return v

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> Assumption:
        return cls.model_validate(d)



_PYTHON_PATTERNS = re.compile(
    r"\bpython\b(?!\s*[\d])",   # "python" not followed by a version number
    re.IGNORECASE,
)
_RUSSIAN_WORD = re.compile(
    r"[а-яёА-ЯЁ]{3,}",
)
_ENGLISH_WORD = re.compile(
    r"\b[a-zA-Z]{4,}\b",
)
_RUN_COMMAND_PATTERNS = re.compile(
    r"\b(запусти|выполни|run\s+the|execute|launch|start\s+the)\b",
    re.IGNORECASE,
)
_FILE_WITHOUT_PATH = re.compile(
    r"\b(файл|файле|файла|the\s+file|this\s+file)\b(?![\s:]*(\/|\\|[a-zA-Z]:))",
    re.IGNORECASE,
)

_TOOL_ASSUMPTIONS: dict[str, list[tuple[str, AssumptionCategory, float]]] = {
    "file_read": [
        ("The target file is UTF-8 encoded.", "file_encoding", 0.85),
    ],
    "file_write": [
        ("Write permissions are available inside the workspace.", "workspace_permission", 0.90),
    ],
    "web_search": [
        ("Public internet access is available from the agent host.", "network_access", 0.85),
    ],
    "web_fetch": [
        ("Public internet access is available from the agent host.", "network_access", 0.85),
        ("The target URL serves text/HTML content.", "file_format", 0.75),
    ],
    "shell_exec": [
        ("The workspace shell environment is correctly configured.", "tool_availability", 0.80),
        ("The requested command is safe to run inside the workspace.", "scope", 0.80),
    ],
    "run_tests": [
        ("pytest is installed and the test suite is runnable.", "tool_availability", 0.85),
    ],
}

_PY_FILE_PATTERN = re.compile(r"\.py\b", re.IGNORECASE)


def extract_from_question(
    question: str,
    run_id: str = "",
    known_language: str | None = None,
) -> list[Assumption]:
    """Extract implicit assumptions from the raw question text."""
    assumptions: list[Assumption] = []

    # If the user profile knows the language, use it directly (source="profile").
    if known_language is not None:
        if known_language == "ru":
            assumptions.append(Assumption(
                run_id=run_id,
                text="The user expects a Russian-language response.",
                category="language",
                confidence=0.95,
                source="profile",
            ))
        elif known_language == "en":
            assumptions.append(Assumption(
                run_id=run_id,
                text="The user expects an English-language response.",
                category="language",
                confidence=0.95,
                source="profile",
            ))
    else:
        ru_count = len(_RUSSIAN_WORD.findall(question))
        en_count = len(_ENGLISH_WORD.findall(question))
        if ru_count >= 3 and en_count < ru_count:
            assumptions.append(Assumption(
                run_id=run_id,
                text="The user expects a Russian-language response.",
                category="language",
                confidence=0.90,
                source="question",
            ))
        elif en_count >= 4 and ru_count == 0:
            assumptions.append(Assumption(
                run_id=run_id,
                text="The user expects an English-language response.",
                category="language",
                confidence=0.90,
                source="question",
            ))

    if _PYTHON_PATTERNS.search(question):
        assumptions.append(Assumption(
            run_id=run_id,
            text="Python 3 (current stable release) is the intended runtime.",
            category="python_version",
            confidence=0.75,
            source="question",
        ))

    if _RUN_COMMAND_PATTERNS.search(question):
        assumptions.append(Assumption(
            run_id=run_id,
            text="The requested operation is scoped to the local workspace.",
            category="scope",
            confidence=0.80,
            source="question",
        ))

    if _FILE_WITHOUT_PATH.search(question):
        assumptions.append(Assumption(
            run_id=run_id,
            text="The referenced file is located inside the workspace root.",
            category="scope",
            confidence=0.80,
            source="question",
        ))

    return assumptions


def extract_from_plan(
    sources: list[dict],
    question: str = "",
    run_id: str = "",
) -> list[Assumption]:
    """Extract implicit assumptions from a planner's tool selection (``sources`` = list of {tool, arguments} dicts). Pure function."""
    assumptions: list[Assumption] = []
    seen_texts: set[str] = set()

    for step in sources:
        tool_name: str = step.get("tool", "")
        args: dict = step.get("arguments", {}) or {}

        for key, tool_assumptions in _TOOL_ASSUMPTIONS.items():
            if key in tool_name:
                for text, category, conf in tool_assumptions:
                    if text not in seen_texts:
                        seen_texts.add(text)
                        assumptions.append(Assumption(
                            run_id=run_id,
                            text=text,
                            category=category,
                            confidence=conf,
                            source="planner",
                        ))

        if "file_read" in tool_name:
            path_arg = str(args.get("path", "") or args.get("file", "") or "")
            if _PY_FILE_PATTERN.search(path_arg):
                extra = "The target Python file is expected to be syntactically valid."
                if extra not in seen_texts:
                    seen_texts.add(extra)
                    assumptions.append(Assumption(
                        run_id=run_id,
                        text=extra,
                        category="file_format",
                        confidence=0.70,
                        source="planner",
                    ))

    return assumptions



class AssumptionRegistry:
    """Mutable in-memory store for one run's assumptions (no locking: one synchronous run)."""

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self._items: list[Assumption] = []
        # Ids loaded from the persistent store at run start; excluded from the end-of-run save to avoid duplicates.
        self._restored_ids: set[str] = set()


    def register(
        self,
        text: str,
        category: AssumptionCategory = "general",
        confidence: float = 0.80,
        source: str = "heuristic",
    ) -> Assumption:
        """Add one explicit assumption. Returns the created object."""
        a = Assumption(
            run_id=self.run_id,
            text=text,
            category=category,
            confidence=confidence,
            source=source,
        )
        self._items.append(a)
        return a

    def register_many(self, assumptions: list[Assumption]) -> None:
        """Bulk-register pre-built Assumption objects."""
        self._items.extend(assumptions)

    def restore_from_store(self, assumptions: list[Assumption]) -> None:
        """Load previously persisted assumptions, marked restored so they are not written again."""
        for a in assumptions:
            self._restored_ids.add(a.id)
        self._items.extend(assumptions)

    def mark_verified(self, assumption_id: str, *, verified: bool) -> bool:
        """Mark an assumption as confirmed (True) or contradicted (False).

        Returns True if the id was found, False otherwise.
        """
        for a in self._items:
            if a.id == assumption_id:
                object.__setattr__(a, "verified", verified)
                return True
        return False


    @property
    def assumptions(self) -> list[Assumption]:
        return list(self._items)

    @property
    def new_assumptions(self) -> list[Assumption]:
        """Assumptions not restored from the store, for end-of-run persistence."""
        return [a for a in self._items if a.id not in self._restored_ids]

    @property
    def active(self) -> list[Assumption]:
        """Assumptions that have not been explicitly contradicted."""
        return [a for a in self._items if a.verified is not False]

    def __len__(self) -> int:
        return len(self._items)


    def to_prompt_block(self) -> str:
        """Render active assumptions as an XML block for the synthesizer prompt."""
        active = self.active
        if not active:
            return ""
        lines = ["<assumptions>"]
        for a in active:
            conf_pct = int(a.confidence * 100)
            verified_tag = ""
            if a.verified is True:
                verified_tag = " [confirmed]"
            # Escape XML special chars to prevent tag injection into the prompt.
            safe_text = html.escape(a.text, quote=False)
            lines.append(
                f"- [{a.category}] {safe_text} (confidence={conf_pct}%){verified_tag}"
            )
        lines.append("</assumptions>")
        return "\n".join(lines)

    def to_log_payload(self) -> list[dict]:
        """Compact payload for trace events."""
        return [
            {
                "id": a.id,
                "text": a.text,
                "category": a.category,
                "confidence": a.confidence,
                "source": a.source,
            }
            for a in self._items
        ]



class AssumptionStore:
    """Append-only JSONL store for Assumption objects across runs."""

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        #: Rows the LAST read could not parse; describes that read only.
        self.last_dropped_rows = 0


    def save(self, assumption: Assumption) -> None:
        """Append one assumption. O(1) write."""
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [assumption.to_dict()])

    def save_many(self, assumptions: list[Assumption]) -> int:
        """Append NEW assumptions; a repeat of the same (category, text, verified) younger than a day is skipped. Returns the number written."""
        if not assumptions:
            return 0
        with state_file_lock(self.path):
            recent = _recent_keys(read_state_jsonl_unlocked(self.path) if self.path.exists() else [])
            payloads = []
            for a in assumptions:
                key = (a.category, a.text.strip(), a.verified)
                if recent.get(key, datetime.min.replace(tzinfo=timezone.utc)) > a.created_at - _REPEAT_AFTER:
                    continue
                recent[key] = a.created_at
                payloads.append(a.to_dict())
            if payloads:
                append_state_jsonl_unlocked(self.path, payloads)
        return len(payloads)


    def compact(self, *, keep_last: int = 500, dry_run: bool = False) -> dict:
        """Dedupe (newest row per (category, text) wins) and keep at most ``keep_last`` rows; ``dry_run`` only counts.
        Rows without usable category/text strings are kept as unique rather than merged.
        """
        if keep_last < 0:
            raise ValueError(f"keep_last must be >= 0, got {keep_last}")
        with state_file_lock(self.path):
            if not self.path.exists():
                return {
                    "scanned": 0, "duplicates_removed": 0,
                    "over_cap_removed": 0, "kept": 0, "dry_run": dry_run,
                }
            rows = read_state_jsonl_unlocked(self.path)
            scanned = len(rows)
            deduped: list[dict] = []
            seen: set[tuple[str, str]] = set()
            for row in reversed(rows):
                category = row.get("category")
                text = row.get("text")
                if not (
                    isinstance(category, str) and category
                    and isinstance(text, str) and text
                ):
                    deduped.append(row)
                    continue
                key = (category, text)
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(row)
            deduped.reverse()
            duplicates_removed = scanned - len(deduped)
            kept_rows = deduped[-keep_last:] if keep_last > 0 else []
            over_cap_removed = len(deduped) - len(kept_rows)
            if not dry_run and (duplicates_removed or over_cap_removed):
                rewrite_state_jsonl_unlocked(self.path, kept_rows)
            return {
                "scanned": scanned,
                "duplicates_removed": duplicates_removed,
                "over_cap_removed": over_cap_removed,
                "kept": len(kept_rows),
                "dry_run": dry_run,
            }


    def _load_all(self) -> list[Assumption]:
        if not self.path.exists():
            # The counter describes THIS read, so the no-file path clears it too.
            self.last_dropped_rows = 0
            return []
        rows = read_state_jsonl_unlocked(self.path)
        result: list[Assumption] = []
        dropped = 0
        for row in rows:
            try:
                result.append(Assumption.from_dict(row))
            except Exception as exc:  # noqa: BLE001 — reason stated above
                # Skipping a bad row stays, but is counted so the caller sees why the list is shorter.
                dropped += 1
                logger.warning(
                    "assumption row dropped (%s): id=%s",
                    exc, (row or {}).get("id", "?") if isinstance(row, dict) else "?",
                )
        self.last_dropped_rows = dropped
        return result

    def load_by_run(self, run_id: str) -> list[Assumption]:
        """Return all assumptions recorded for a specific run."""
        return [a for a in self._load_all() if a.run_id == run_id]

    def load_recent(self, n: int = 50) -> list[Assumption]:
        """Return the last *n* assumptions (most-recent-first)."""
        all_items = self._load_all()
        return list(reversed(all_items[-n:])) if all_items else []

    def load_recent_runs(self, n: int = 5) -> dict[str, list[Assumption]]:
        """Return assumptions grouped by the last *n* distinct run_ids."""
        all_items = self._load_all()
        seen_runs: list[str] = []
        grouped: dict[str, list[Assumption]] = {}
        for a in reversed(all_items):
            if a.run_id not in seen_runs:
                seen_runs.append(a.run_id)
            if len(seen_runs) > n and a.run_id not in seen_runs[:n]:
                break
            grouped.setdefault(a.run_id, []).append(a)
        return grouped
