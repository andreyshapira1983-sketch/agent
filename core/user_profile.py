"""User Profile — Layer 4 (User Mental Model).

Tracks WHO the user is across sessions so the agent can adapt its
output style, terminology level, and verbosity without asking every time.

Design principles
-----------------
* No LLM calls — all updates are heuristic, deterministic, O(n).
* Single JSONL file per workspace (one profile record, overwritten on update).
* Expertise signals accumulate: the profile only moves *toward* a level,
  never jumps abruptly. This avoids a single jargon word permanently
  labelling a novice user as an expert.
* Preference signals (verbosity, language) are sticky: explicit user markers
  ("кратко", "подробно") override the inferred default and stay set until
  the user explicitly changes them.
* Profile is injected into the synthesizer as a ``<user_profile>`` block.
  The planner never sees it — planning does not depend on who the user is.

Expertise levels
----------------
NOVICE       — short, high-level questions; no technical jargon detected.
               Agent uses plain language, analogies, step-by-step guidance.
INTERMEDIATE — mix of technical and conversational language; some domain
               familiarity. Default level for new profiles.
EXPERT       — sustained use of deep technical terms, architecture commands,
               low-level Python/system references. Agent assumes domain
               knowledge, uses precise terminology, skips basics.

Verbosity levels
----------------
BRIEF        — user prefers short, punchy replies.
NORMAL       — balanced; length matches question complexity. (default)
DETAILED     — user explicitly asked for comprehensive treatment.
"""
from __future__ import annotations

import re
from datetime import datetime, timezone
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


# ---------------------------------------------------------------------------
# Type aliases / enums
# ---------------------------------------------------------------------------

ExpertiseLevel = Literal["novice", "intermediate", "expert"]
VerbosityLevel = Literal["brief", "normal", "detailed"]


def _now() -> datetime:
    return datetime.now(timezone.utc)


# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

class UserProfile(BaseModel):
    """Persistent user mental model."""

    id: str = Field(default_factory=lambda: new_id("uprof"))
    expertise: ExpertiseLevel = "intermediate"
    verbosity: VerbosityLevel = "normal"
    technical: bool = True          # prefer technical vocabulary
    language: str = "ru"            # detected from first interactions
    interests: list[str] = Field(default_factory=list)   # domain topics
    interaction_count: int = 0
    expert_signals: int = 0         # cumulative expert-signal score
    novice_signals: int = 0         # cumulative novice-signal score
    verbosity_locked: bool = False  # True when user explicitly set verbosity
    language_locked: bool = False   # True when user explicitly set language
    created_at: datetime = Field(default_factory=_now)
    updated_at: datetime = Field(default_factory=_now)

    @field_validator("language")
    @classmethod
    def _language_is_bcp47_safe(cls, v: str) -> str:
        """Accept short BCP-47 language tags only (e.g. 'ru', 'en', 'zh-CN')."""
        import re as _re
        if not _re.match(r"^[a-zA-Z]{2,8}(-[a-zA-Z0-9]{1,8})*$", v):
            raise ValueError(
                f"language must be a BCP-47 tag like 'ru' or 'en-US', got {v!r}"
            )
        return v.lower()

    @field_validator("interaction_count", "expert_signals", "novice_signals")
    @classmethod
    def _non_negative(cls, v: int) -> int:
        if v < 0:
            raise ValueError(f"counter must be non-negative, got {v}")
        return v


# ---------------------------------------------------------------------------
# Signal detection
# ---------------------------------------------------------------------------

# Expert signals: deep technical jargon or architecture commands.
_EXPERT_PATTERNS = re.compile(
    r"\b("
    r"GIL|PEP\s*\d+|CPython|bytecode|AST|coroutine|asyncio|"
    r"deadlock|race\s*condition|atomic|mutex|semaphore|"
    r"architecture|planner|synthesizer|policy\s*gate|"
    r"episodic|procedural|knowledge\s*pipeline|source\s*ranker|"
    r"replan|compensation|idempotency|provenance|"
    r"biased\s*reference\s*counting|immortalization|"
    r"free[\s._-]?thread(?:ing)?|free_thread|nogil|disable[._-]?gil|"
    r"OWASP|CVE|injection|XSS|CSRF|"
    r"RFC\s*\d+|OAuth|JWT|HMAC|SHA-?\d+|"
    r"kernel|syscall|mmap|heap|stack\s*frame|"
    r"O\([nN]\)|O\(log|amortized|"
    r"inference|embedding|token\s*budget|context\s*window"
    r")\b",
    re.IGNORECASE,
)

# Novice signals: simple meta-questions, conceptual "what is".
_NOVICE_PATTERNS = re.compile(
    r"\b("
    r"что\s+такое|как\s+работает|почему\s+это|как\s+пользоваться|"
    r"что\s+значит|объясни\s+просто|для\s+начинающих|"
    r"what\s+is\s+a?\b|how\s+does\s+it\s+work|what\s+does\s+\w+\s+mean|"
    r"explain\s+simply|for\s+beginners|is\s+this\s+correct\?|"
    r"am\s+I\s+doing\s+this\s+right"
    r")\b",
    re.IGNORECASE,
)

# Explicit brevity request.
_BRIEF_PATTERNS = re.compile(
    r"\b(кратко|вкратце|коротко|одним\s+словом|tl;?dr|brief(?:ly)?|"
    r"in\s+short|short\s+answer|one.liner|quick\s+answer)\b",
    re.IGNORECASE,
)

# Explicit detail request.
# NOTE: bare "подробнее" is intentionally excluded — it is a comparative
# adverb that frequently modifies a specific object ("покажи тесты подробнее")
# rather than expressing a global verbosity preference.  Only directive
# forms (paired with a communication verb) trigger the lock.
_DETAILED_PATTERNS = re.compile(
    r"\b(подробно|детально|развёрнуто|расскажи\s+всё|"
    r"объясни\s+подробно|full\s+explanation|in\s+detail|"
    r"comprehensive|step.by.step|thoroughly)\b"
    r"|\b(отвечай|говори|пиши|объясняй|расскажи|объясни)\s+подробн\w*\b",
    re.IGNORECASE,
)

# Russian language markers (common functional words).
_RU_MARKERS = re.compile(
    r"\b(и|в|на|с|по|из|для|это|что|как|тот|тут|там|всё|его|её|их)\b"
)

# English language markers.
_EN_MARKERS = re.compile(
    r"\b(the|and|of|to|in|is|are|that|this|with|for|from|have|it)\b",
    re.IGNORECASE,
)

# Domain interest keywords → canonical topic label.
_INTEREST_KEYWORDS: dict[str, str] = {
    # Python internals
    "gil": "python-internals",
    "cpython": "python-internals",
    "bytecode": "python-internals",
    "free.thread": "python-internals",
    "asyncio": "python-async",
    "coroutine": "python-async",
    # Agent architecture
    "planner": "agent-architecture",
    "synthesizer": "agent-architecture",
    "architecture": "agent-architecture",
    "control.loop": "agent-architecture",
    # Memory
    "episodic": "agent-memory",
    "procedural": "agent-memory",
    "memory": "agent-memory",
    "knowledge": "agent-memory",
    # Security
    "injection": "security",
    "owasp": "security",
    "cve": "security",
    "secret": "security",
    # Testing
    "pytest": "testing",
    "coverage": "testing",
    "test": "testing",
    # Multi-agent
    "multi.agent": "multi-agent",
    "subagent": "multi-agent",
    "team": "multi-agent",
}

_INTEREST_RE = re.compile(
    "|".join(re.escape(k) for k in _INTEREST_KEYWORDS),
    re.IGNORECASE,
)


# ---------------------------------------------------------------------------
# Heuristic update
# ---------------------------------------------------------------------------

def _detect_language(text: str) -> str | None:
    """Return 'ru', 'en', or None (ambiguous)."""
    ru = len(_RU_MARKERS.findall(text))
    en = len(_EN_MARKERS.findall(text))
    if ru > en and ru >= 2:
        return "ru"
    if en > ru and en >= 2:
        return "en"
    return None


def _extract_interests(text: str) -> list[str]:
    """Return list of canonical interest labels found in *text*."""
    found: set[str] = set()
    for m in _INTEREST_RE.finditer(text):
        key = m.group(0).casefold().replace("-", ".")
        # Try direct lookup; fall back to any key whose pattern matches.
        for k, label in _INTEREST_KEYWORDS.items():
            if re.match(k, key, re.IGNORECASE):
                found.add(label)
                break
    return sorted(found)


def update_profile(
    profile: UserProfile,
    question: str,
    response: str = "",
) -> UserProfile:
    """Return a NEW profile updated from one interaction.

    Pure function — the original *profile* is not mutated.

    Rules
    -----
    * verbosity_locked / language_locked: explicit user signals set these
      and prevent further heuristic overrides.
    * Expertise is a sliding average: expert signals increment
      ``expert_signals``, novice signals increment ``novice_signals``.
      The current level is re-derived every update from the ratio.
    * Interests are additive (set union), capped at 20 topics.
    """
    data = profile.model_dump()
    text = question + " " + response

    data["interaction_count"] += 1
    data["updated_at"] = _now()

    # --- Verbosity (sticky if locked) ---
    if not data["verbosity_locked"]:
        if _BRIEF_PATTERNS.search(question):
            data["verbosity"] = "brief"
            data["verbosity_locked"] = True
        elif _DETAILED_PATTERNS.search(question):
            data["verbosity"] = "detailed"
            data["verbosity_locked"] = True
    else:
        # Allow re-lock with opposing signal.
        if _BRIEF_PATTERNS.search(question):
            data["verbosity"] = "brief"
        elif _DETAILED_PATTERNS.search(question):
            data["verbosity"] = "detailed"

    # --- Language (sticky if locked) ---
    if not data["language_locked"]:
        lang = _detect_language(question)
        if lang is not None:
            data["language"] = lang
            # Lock after 3 consistent interactions.
            if data["interaction_count"] >= 3:
                data["language_locked"] = True

    # --- Expert / novice signals ---
    expert_hits = len(_EXPERT_PATTERNS.findall(text))
    novice_hits = len(_NOVICE_PATTERNS.findall(text))
    data["expert_signals"] += expert_hits
    data["novice_signals"] += novice_hits

    # Reclassify expertise.
    total = data["expert_signals"] + data["novice_signals"]
    if total > 0:
        expert_ratio = data["expert_signals"] / total
        if expert_ratio >= 0.65:
            data["expertise"] = "expert"
        elif expert_ratio <= 0.25:
            data["expertise"] = "novice"
        else:
            data["expertise"] = "intermediate"

    # --- Technical vocabulary flag ---
    # Expert always prefers technical; novice prefers plain by default.
    if data["expertise"] == "expert":
        data["technical"] = True
    elif data["expertise"] == "novice" and not data["verbosity_locked"]:
        data["technical"] = False

    # --- Interests ---
    new_interests = _extract_interests(question)
    merged = list(dict.fromkeys(data["interests"] + new_interests))
    data["interests"] = merged[:20]  # cap at 20

    return UserProfile(**data)


# ---------------------------------------------------------------------------
# Prompt injection
# ---------------------------------------------------------------------------

def profile_to_prompt_block(profile: UserProfile) -> str:
    """Render a ``<user_profile>`` XML block for synthesizer injection.

    P2 — STYLE-ONLY block. Only fields that influence presentation
    (verbosity, vocabulary, language, expertise level for terminology
    depth) are emitted. Domain-tied fields like ``interests`` and
    ``interaction_count`` are deliberately withheld so the LLM cannot
    use the operator's past-topic history to narrow or steer the
    current answer's domain. The current question alone defines scope.
    """
    vocab = "technical" if profile.technical else "plain"
    return (
        "<user_profile>\n"
        f"expertise: {profile.expertise}\n"
        f"verbosity: {profile.verbosity}\n"
        f"vocabulary: {vocab}\n"
        f"language: {profile.language}\n"
        "</user_profile>"
    )


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------

class UserProfileStore:
    """Single-record JSONL store for the user profile.

    Design: the file holds at most ONE active record. Each ``save`` appends
    a new snapshot; ``load`` returns the LAST valid record. This gives a
    free audit trail of profile evolution without requiring a separate
    history file.
    """

    def __init__(self, path: Path | str) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def load(self) -> UserProfile | None:
        """Return the most recent profile, or None if no valid record exists."""
        if not self.path.exists():
            return None
        records = read_state_jsonl_unlocked(self.path)
        if not records:
            return None
        # Walk backwards to find the latest valid record.
        for raw in reversed(records):
            try:
                return UserProfile(**raw)
            except Exception:  # noqa: BLE001 — corrupted record, skip
                continue
        return None

    def save(self, profile: UserProfile) -> None:
        """Append one profile snapshot (O(1) write)."""
        with state_file_lock(self.path):
            append_state_jsonl_unlocked(
                self.path, [profile.model_dump(mode="json")]
            )

    def load_or_default(self) -> UserProfile:
        """Return the stored profile, or a fresh default if none exists."""
        return self.load() or UserProfile()

    def update_from_interaction(
        self,
        question: str,
        response: str = "",
        *,
        base: UserProfile | None = None,
    ) -> UserProfile:
        """Load (or use *base*), update from one interaction, save, return."""
        profile = base if base is not None else self.load_or_default()
        updated = update_profile(profile, question, response)
        self.save(updated)
        return updated
