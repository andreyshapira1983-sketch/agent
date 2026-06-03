"""Assumption Registry — Layer 5 (Explicit Planning Assumptions).

Every time the agent plans a tool call or interprets a question it makes
implicit assumptions: "the file is UTF-8", "Python 3 is intended", "the
web is reachable".  Layer 5 surfaces these as first-class objects so they
can be:

  - **logged** as ``assumption_registered`` trace events (full auditability)
  - **injected** into the synthesizer as an ``<assumptions>`` block so the
    LLM can explicitly acknowledge or caveat them in its answer
  - **persisted** per-run in ``data/assumptions.jsonl`` for retrospective
    analysis
  - **surfaced** to the operator via ``:assumptions`` in the REPL

Design principles
-----------------
* No LLM calls — all extraction is heuristic regex, deterministic, O(n).
* Pure extraction functions — no side effects, easy to unit-test.
* ``AssumptionRegistry`` is in-memory / per-run; ``AssumptionStore`` is the
  durable JSONL layer.
* The update to the run answer must never be aborted by an assumption error —
  all registry operations are wrapped defensively in ``loop.py``.

Assumption categories
---------------------
file_encoding       — character encoding of a target file.
file_format         — expected structure / syntax of a target file.
language            — expected response / question language.
python_version      — which Python version is intended.
tool_availability   — presence of a required CLI tool or library.
network_access      — availability of public internet.
workspace_permission — write/execute rights in the workspace.
user_intent         — interpretation of an ambiguous goal.
scope               — whether the action is limited to the workspace.
general             — catch-all for other implicit assumptions.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field

from core.ids import new_id
from core.state_integrity import (
    append_state_jsonl_unlocked,
    read_state_jsonl_unlocked,
    state_file_lock,
)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class Assumption(BaseModel):
    """A single explicit assumption made during one agent run."""

    id: str = Field(default_factory=lambda: new_id("asmp"))
    run_id: str = ""                   # trace_id of the owning run
    text: str                          # human-readable statement
    category: AssumptionCategory = "general"
    confidence: float = 0.80           # 0.0–1.0
    source: str = "heuristic"          # "planner", "question", "kernel", "heuristic"
    verified: bool | None = None       # None=not yet checked
    created_at: datetime = Field(default_factory=_now)

    def to_dict(self) -> dict:
        return self.model_dump(mode="json")

    @classmethod
    def from_dict(cls, d: dict) -> "Assumption":
        return cls.model_validate(d)


# ---------------------------------------------------------------------------
# Heuristic extractors (pure functions — no side effects)
# ---------------------------------------------------------------------------

# Question-level signals
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

# Tool-level signals (keyed by tool name substrings)
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

# .py extension → additional format assumption
_PY_FILE_PATTERN = re.compile(r"\.py\b", re.IGNORECASE)


def extract_from_question(
    question: str,
    run_id: str = "",
    known_language: str | None = None,
) -> list[Assumption]:
    """Extract implicit assumptions from the raw question text.

    Pure function — no I/O, no side effects.

    Parameters
    ----------
    known_language:
        If the ``UserProfile`` (Layer 4) already has a confirmed language
        (``"ru"`` or ``"en"``), pass it here so the heuristic is skipped and
        replaced by a higher-confidence profile-backed assumption.  When
        ``None`` the regex heuristic runs as usual.
    """
    assumptions: list[Assumption] = []

    # Language assumption
    # Layer 4→5 bridge: if the user profile already knows the language,
    # use that signal directly (higher confidence, source="profile").
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
        # Fall back to heuristic — only when clearly one-sided
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

    # Python version — "python" without a version specifier
    if _PYTHON_PATTERNS.search(question):
        assumptions.append(Assumption(
            run_id=run_id,
            text="Python 3 (current stable release) is the intended runtime.",
            category="python_version",
            confidence=0.75,
            source="question",
        ))

    # Run-command scope
    if _RUN_COMMAND_PATTERNS.search(question):
        assumptions.append(Assumption(
            run_id=run_id,
            text="The requested operation is scoped to the local workspace.",
            category="scope",
            confidence=0.80,
            source="question",
        ))

    # File without explicit path
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
    """Extract implicit assumptions from a planner's tool selection.

    ``sources`` is the list of ``{"tool": ..., "arguments": ...}`` dicts
    returned by ``LLMPlanner.plan()``.  Pure function — no I/O.
    """
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

        # Extra: file with .py extension → syntax assumption
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


# ---------------------------------------------------------------------------
# In-memory registry (one per agent run)
# ---------------------------------------------------------------------------

class AssumptionRegistry:
    """Mutable in-memory store for a single agent run's assumptions.

    Thread-safety: this object is created and consumed within one synchronous
    ``AgentLoop.run()`` call so no locking is required.
    """

    def __init__(self, run_id: str = "") -> None:
        self.run_id = run_id
        self._items: list[Assumption] = []
        # IDs loaded from the persistent store at run start (Layer 2→5 bridge).
        # These are excluded from the end-of-run save_many call to avoid
        # duplication in the JSONL file.
        self._restored_ids: set[str] = set()

    # ---------- writes ----------

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
        """Load assumptions that were previously persisted (Layer 2→5 bridge).

        These are added to the registry but marked as *restored* so they are
        not written to the store again at end-of-run.
        """
        for a in assumptions:
            self._restored_ids.add(a.id)
        self._items.extend(assumptions)

    def mark_verified(self, assumption_id: str, *, verified: bool) -> bool:
        """Mark an assumption as confirmed (True) or contradicted (False).

        Returns True if the id was found, False otherwise.
        """
        for a in self._items:
            if a.id == assumption_id:
                # Pydantic v2 — use model_copy to stay immutable-ish, or
                # just set the field directly (model_config allows it).
                object.__setattr__(a, "verified", verified)
                return True
        return False

    # ---------- reads ----------

    @property
    def assumptions(self) -> list[Assumption]:
        return list(self._items)

    @property
    def new_assumptions(self) -> list[Assumption]:
        """Assumptions that were NOT restored from the store — only new ones.

        Use this for end-of-run persistence to avoid JSONL duplication.
        """
        return [a for a in self._items if a.id not in self._restored_ids]

    @property
    def active(self) -> list[Assumption]:
        """Assumptions that have not been explicitly contradicted."""
        return [a for a in self._items if a.verified is not False]

    def __len__(self) -> int:
        return len(self._items)

    # ---------- prompt block ----------

    def to_prompt_block(self) -> str:
        """Render active assumptions as an XML block for the synthesizer.

        The block is injected into the user prompt so the LLM can acknowledge
        or caveat these assumptions in its answer.
        """
        active = self.active
        if not active:
            return ""
        lines = ["<assumptions>"]
        for a in active:
            conf_pct = int(a.confidence * 100)
            verified_tag = ""
            if a.verified is True:
                verified_tag = " [confirmed]"
            lines.append(
                f"- [{a.category}] {a.text} (confidence={conf_pct}%){verified_tag}"
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


# ---------------------------------------------------------------------------
# Persistent store
# ---------------------------------------------------------------------------

class AssumptionStore:
    """Append-only JSONL store for Assumption objects across runs.

    Each line in the JSONL file is a single serialised ``Assumption``.
    Corrupted lines are silently skipped.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    # ---------- writes ----------

    def save(self, assumption: Assumption) -> None:
        """Append one assumption. O(1) write."""
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, [assumption.to_dict()])

    def save_many(self, assumptions: list[Assumption]) -> int:
        """Append multiple assumptions in one lock acquisition."""
        if not assumptions:
            return 0
        payloads = [a.to_dict() for a in assumptions]
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(self.path, payloads)
        return len(payloads)

    # ---------- reads ----------

    def _load_all(self) -> list[Assumption]:
        if not self.path.exists():
            return []
        rows = read_state_jsonl_unlocked(self.path)
        result: list[Assumption] = []
        for row in rows:
            try:
                result.append(Assumption.from_dict(row))
            except Exception:
                pass
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
