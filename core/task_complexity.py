"""Task Complexity Assessment — automatic model tier selection.

The agent evaluates task complexity BEFORE making an LLM call and selects
the appropriate model tier:

  LIGHT    → fast, cheap  (haiku-family, mini-family)
  STANDARD → balanced     (sonnet-family, standard GPT)
  DEEP     → powerful     (opus-family, o1/o3-family)

No LLM call is made here — this is a pure rule-based heuristic.

Model names are NEVER hardcoded here.
Actual model selection is in core/model_catalog.py which queries the
provider's own API (anthropic.models.list / openai.models.list) and
classifies results into tiers by naming pattern.

Env override (optional — otherwise auto-discovered):
  AGENT_MODEL_TIER_LIGHT    = <model-id>
  AGENT_MODEL_TIER_STANDARD = <model-id>
  AGENT_MODEL_TIER_DEEP     = <model-id>
"""
from __future__ import annotations

import re
from enum import Enum


# ── tier enum ─────────────────────────────────────────────────────────────────

class ComplexityTier(str, Enum):
    LIGHT    = "light"
    STANDARD = "standard"
    DEEP     = "deep"




# ── role-level overrides ──────────────────────────────────────────────────────

# Roles that are always LIGHT regardless of task text.
_ALWAYS_LIGHT_ROLES: frozenset[str] = frozenset({
    "memory_summary",  # summaries never need a frontier model
})


# ── signals ───────────────────────────────────────────────────────────────────

# Any of these substrings in the task text → DEEP tier.
# Multi-lingual (RU + EN). Substrings, not whole words — so "архитектур"
# matches "архитектура", "архитектуры", "архитектурный", etc.
_DEEP_SIGNALS: frozenset[str] = frozenset({
    # Architecture & system design
    "архитектур",   "architecture",   "system design",  "design system",
    "спроектируй",  "спроектировать",
    # Full audit / security
    "аудит",        "audit",          "security audit", "аудит безопасности",
    "pentest",      "penetration",    "уязвимост",
    # Full implementation / from scratch
    "написать и протестировать",  "write and test",  "write, test",
    "разработать с нуля",         "from scratch",    "полная реализация",
    "full implementation",        "полный",
    # Research & strategic analysis
    "исследуй вес", "research all",   "compare all",    "сравни вс",
    "evaluate all", "оцени вс",       "оцени риск",     "risk assessment",
    "стратеги",     "strategy",       "roadmap",
    # Complex multi-step
    "многоэтапн",   "multi-step",     "multi-phase",    "комплексн",
    "complex analysis", "комплексный анализ",
    # Comprehensive documentation
    "полную документацию", "full documentation", "complete documentation",
})

# Any of these substrings + short text → LIGHT tier.
_LIGHT_SIGNALS: frozenset[str] = frozenset({
    # Greetings
    "привет",   "hello",    "hi",     "hey",    "добрый",
    # Simple Q&A
    "что такое", "what is", "define", "скажи",  "say",
    "объясни в одном", "explain in one",
    # Status & health
    "статус",   "status",   "ping",   "health", "готов",
    "да или нет", "yes or no", "true or false",
    # Quick ops
    "переведи",  "translate", "перевод",
    "суммаризуй", "summarize", "summarise", "summary",
    "кратко",   "briefly",   "quick",  "быстро",
    # Simple lists / lookups
    "перечисли", "list all",  "покажи", "show me",
    "найди одн", "find one",  "get one",
    # Version / info
    "версия",   "version",   "changelog",
})

# Short text under this many characters always → LIGHT (unless DEEP signal).
_SHORT_TEXT_THRESHOLD = 45


# ── public API ────────────────────────────────────────────────────────────────

def assess_complexity(
    text: str,
    *,
    role: str = "planner",
) -> ComplexityTier:
    """Rule-based complexity assessment. No LLM call. O(n) in signal count.

    Priority order
    ──────────────
    1. Role-level overrides  (e.g. memory_summary → always LIGHT)
    2. Empty / non-string    → STANDARD
    3. DEEP signals          (substring match in normalized text) → DEEP
    4. LIGHT signals         (substring match) AND text shorter than
       ``_SHORT_TEXT_THRESHOLD * 4`` (~180 chars) → LIGHT
    5. Default               → STANDARD

    There is intentionally NO "any very short text → LIGHT" rule: short text
    with no recognized LIGHT signal still falls through to STANDARD (the
    conservative default). See ``test_very_short_text_no_signals_is_standard``.
    """
    if role in _ALWAYS_LIGHT_ROLES:
        return ComplexityTier.LIGHT

    if not isinstance(text, str):
        return ComplexityTier.STANDARD
    stripped = text.strip()
    if not stripped:
        return ComplexityTier.STANDARD

    normalized = stripped.casefold()

    # DEEP check first — highest priority
    for signal in _DEEP_SIGNALS:
        if signal in normalized:
            return ComplexityTier.DEEP

    # LIGHT check — only when text is short enough
    is_short = len(stripped) < _SHORT_TEXT_THRESHOLD * 4  # ~180 chars
    if is_short:
        for signal in _LIGHT_SIGNALS:
            if signal in normalized:
                return ComplexityTier.LIGHT

    return ComplexityTier.STANDARD


def tier_label(tier: ComplexityTier) -> str:
    """Human-readable label for logging/display."""
    return {
        ComplexityTier.LIGHT:    "light (fast/cheap)",
        ComplexityTier.STANDARD: "standard (balanced)",
        ComplexityTier.DEEP:     "deep (powerful)",
    }[tier]


# ── live grounding signals ────────────────────────────────────────────────────
#
# When a task mentions time-sensitive keywords the agent must NOT rely on
# its training-data snapshot. Instead the planner should add a web_search
# step to retrieve fresh information before synthesising the answer.
#
# False positives are cheap (one extra search). False negatives produce
# stale / wrong answers. So the signal list is deliberately broad.

_LIVE_GROUNDING_SIGNALS: frozenset[str] = frozenset({
    # ── English temporal / recency ──
    "latest",   "newest",   "current",   "today",    "right now",
    "as of",    "recent",   "recently",
    "just released", "just launched", "just announced", "just dropped",
    "this week", "this month", "this year",
    "2025", "2026", "2027",          # explicit calendar year → always fresh
    # ── English release / news ──
    "release", "released", "launched", "shipped", "new version",
    "changelog", "what's new", "news about",
    "updates on", "updates to", "roadmap",
    # ── English model / AI specific ──
    "latest model", "newest model", "which model", "best model",
    "gpt-5", "o3-mini", "o4", "claude 4", "claude 5",
    "gemini 2", "llama 4", "mistral", "deepseek",
    # ── Russian temporal ──
    "последн",  "актуальн",  "сейчас",   "сегодня",
    "только что", "на данный момент", "в данный момент",
    # ── Russian release / news ──
    "вышел",  "вышла",  "вышло",  "вышли",
    "выпустил", "выпустили", "анонсировал",
    "новост", "что нового",  "обновлен",
    # ── Russian model / AI ──
    "новый claude", "новый gpt", "последняя модель",
    "актуальная модель", "какая модель", "лучшая модель",
})


def needs_live_grounding(text: str) -> bool:
    """Return True when the task likely requires fresh web data.

    Called by the planner to inject a ``[LIVE_GROUNDING=required]`` hint
    into the user prompt, which makes the LLM reliably add ``web_search``
    as the first plan step instead of answering from stale training data.

    This is a keyword heuristic — no LLM call, O(n) in signal count.
    """
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.strip().casefold()
    return any(sig in normalized for sig in _LIVE_GROUNDING_SIGNALS)


# ── cheap-path planner gate ───────────────────────────────────────────────────
#
# Trivial operator chatter (config-flag echoes, greetings, one-line "what is X"
# questions) never needs an external tool. Yet the loop still spends a full
# planner LLM call (~7k input tokens in practice) only to have the planner
# answer "no tools needed" and return an empty plan. ``can_skip_planner``
# lets the loop bypass that wasted call and synthesise the answer directly.
#
# Safety contract
# ---------------
# * Pure function — no I/O, no LLM.
# * POSITIVE-signal gate: returns True only when a trivial signal is present
#   (config-flag pattern or a LIGHT-complexity signal). When in doubt it
#   returns False so the planner still runs — a missed skip only costs one
#   extra planner call, whereas a wrong skip could drop a needed tool step.
# * Any hint that a tool might be required (file hint, tool keyword, live
#   grounding, DEEP task) forces False.

# Substrings that suggest the request may need a tool (read a file, search the
# web, run a command, mutate state, …). Matching is deliberately broad: a false
# positive here just means the planner runs as before (safe), never a wrong skip.
_TOOL_SIGNALS: frozenset[str] = frozenset({
    # EN — file / path / io
    "read", "file", "path", "directory", "folder", "grep", "cat ",
    # EN — web / net
    "search", "google", "http", "url", "fetch", "download", "crawl", "scrape",
    # EN — shell / exec / build
    "run ", "execute", "exec", "shell", "terminal", "command", "install",
    "build", "compile", "deploy", "clone", "commit", "ingest",
    # EN — mutate
    "write", "create", "delete", "remove", "patch", "edit", "modify",
    "refactor", "save",
    # RU — file / io
    "прочит", "читай", "открой", "файл", "папк", "директори", "катал",
    # RU — web
    "поищи", "ищи", "найди", "скачай", "загрузи", "ссылк", "сайт", "загруз",
    # RU — shell / exec / build
    "запусти", "выполни", "установи", "собери", "скомпил", "разверн",
    "склонир", "коммит", "команд", "ингест",
    # RU — mutate
    "напиши", "создай", "сделай", "удали", "исправ", "отредактир",
    "измени", "рефактор", "пропатч", "сохрани",
})

# Config-flag echoes like ``effects=disabled``, ``budget_cap=250/day``,
# ``auto_model_update=false`` — a single ``key=value`` token, no free text.
_CONFIG_FLAG_RE = re.compile(r"^[A-Za-z_][\w.-]*\s*=\s*[^\s=]+$")

# Whole-word greeting / thanks tokens. Matched on tokenized words (never as raw
# substrings) so short tokens like "hi" can't match inside "this"/"which". A
# message counts as a pure greeting only when EVERY word is in this set, so
# "напиши привет" (has a tool verb) or "hi, read the file" never qualify.
_GREETING_WORDS: frozenset[str] = frozenset({
    # EN
    "hello", "hi", "hey", "good", "morning", "evening", "afternoon",
    "thanks", "thank", "you", "greetings",
    # RU
    "привет", "здравствуй", "здравствуйте", "хай", "добрый", "доброе",
    "день", "вечер", "утро", "спасибо", "благодарю",
})

# Above this length an input is no longer "trivial chatter"; let the planner run.
_CHEAP_PATH_MAX_CHARS = 200

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _is_pure_greeting(normalized: str) -> bool:
    """True when *normalized* is a 1–4 word message of only greeting/thanks words."""
    words = _WORD_RE.findall(normalized)
    if not words or len(words) > 4:
        return False
    return all(w in _GREETING_WORDS for w in words)


def can_skip_planner(text: str, *, file_hint: str | None = None) -> bool:
    """Return True when the planner LLM call can be safely skipped.

    The loop uses this to route trivial, no-tool operator input straight to
    the synthesizer (one LLM call) instead of paying for a planner call that
    would only return an empty plan.

    Returns True only when ALL hold:
    * no explicit file hint,
    * text is a short string (``<= _CHEAP_PATH_MAX_CHARS``),
    * no tool-signal keyword is present,
    * the task does not need live/fresh web grounding,
    * the task is not DEEP complexity, AND
    * a positive trivial signal is present — either a ``key=value`` config
      flag echo or a pure greeting / thanks message.

    The positive signal is intentionally narrow: ambiguous "what is X" / "list"
    / "define" phrasing can legitimately require a tool (web_search, file read),
    so those are NOT skipped — a missed skip only costs one extra planner call,
    whereas a wrong skip could drop a needed tool step.

    Pure and deterministic; never raises.
    """
    if file_hint:
        return False
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _CHEAP_PATH_MAX_CHARS:
        return False

    normalized = stripped.casefold()

    # Disqualifiers — any hint a tool may be needed forces the planner to run.
    if any(sig in normalized for sig in _TOOL_SIGNALS):
        return False
    if needs_live_grounding(stripped):
        return False
    if assess_complexity(stripped) is ComplexityTier.DEEP:
        return False

    # Positive trivial signal required.
    if _CONFIG_FLAG_RE.match(stripped):
        return True
    return _is_pure_greeting(normalized)


