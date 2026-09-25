"""Task Complexity Assessment — automatic model tier selection.

No model names here: core/model_catalog.py maps tiers to the provider's actual models.
"""
from __future__ import annotations

import re
from enum import StrEnum

# ── tier enum ─────────────────────────────────────────────────────────────────

class ComplexityTier(StrEnum):
    LIGHT    = "light"
    STANDARD = "standard"
    DEEP     = "deep"


# ── role-level overrides ──────────────────────────────────────────────────────

# Model roles that are always LIGHT regardless of task text.
_ALWAYS_LIGHT_ROLES: frozenset[str] = frozenset({
    "memory_summary",  # summaries never need a frontier model
})

# Task roles (from core.role_router.RoleRouter) never allowed LIGHT, however phrased:
# a weak plan here changes source code. DEEP stays reachable.
_NEVER_LIGHT_TASK_ROLES: frozenset[str] = frozenset({
    "repair",      # root-cause analysis, regression hunting, self-repair
    "programmer",  # writes or edits code
})


# ── signals ───────────────────────────────────────────────────────────────────

# Any of these substrings → DEEP tier. Substrings, not whole words, so Russian
# stems ("архитектур") match every form.
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

# Any of these (word-boundary match) + short text → LIGHT tier.
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

# LIGHT needs a LIGHT signal AND text shorter than this * 4 (~180 chars);
# short text alone never forces LIGHT.
_SHORT_TEXT_THRESHOLD = 45


def _compile_light_signal(signal: str) -> re.Pattern[str]:
    """Compile one LIGHT signal into a boundary-aware pattern.

    Bare substrings let ``"hi"`` match ``"this"``. The left edge is always a word
    boundary; the right edge only for signals ending in an ASCII letter or digit, so
    Cyrillic stems keep prefix matching (``"готов"`` → ``"готовность"``).
    """
    pattern = r"\b" + re.escape(signal)
    if signal and (signal[-1].isascii() and signal[-1].isalnum()):
        pattern += r"\b"
    return re.compile(pattern)


# Sorted only so diagnostics are deterministic; any single match decides.
_LIGHT_SIGNAL_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (signal, _compile_light_signal(signal)) for signal in sorted(_LIGHT_SIGNALS)
)

# ── вопрос о самом агенте ─────────────────────────────────────────────────────
# Нужны оба условия: агента назвали (ты / себя / you) И спрашивают про него самого;
# «покажи, что ты нашёл в файле» — про файл, не про него.
_AGENT_NAMED_RE = re.compile(
    r"\b(?:ты|тебе|тебя|тобой|твой|твоя|твои|твоё|твое|своих|свои|себе|себя|"
    r"you|your|yourself)\b"
)
_AGENT_SUBJECT_RE = re.compile(
    r"(?:о себе|про себя|себя|собой|что ты такое|кто ты|твоя природа|"
    r"твои желания|хотел бы|стремил|развива|цифров|сознан|личност|идентич|"
    r"ошибк|слаб|ограничен|недостат|баг|устроен|устройств|"
    r"about yourself|who are you|what are you|your own|your limitation|"
    r"your weakness|your flaw|your desire)"
)


def asks_about_the_agent(normalized: str) -> bool:
    """Спрашивают ли у агента про него самого? Вход — текст в нижнем регистре."""
    return bool(_AGENT_NAMED_RE.search(normalized) and _AGENT_SUBJECT_RE.search(normalized))


def matched_light_signals(text: str) -> list[str]:
    """Return every LIGHT signal that matches ``text`` under boundary rules (diagnostics)."""
    if not isinstance(text, str):
        return []
    normalized = text.strip().casefold()
    if not normalized:
        return []
    return [signal for signal, pattern in _LIGHT_SIGNAL_PATTERNS if pattern.search(normalized)]


# ── public API ────────────────────────────────────────────────────────────────

def assess_complexity(
    text: str,
    *,
    role: str = "planner",
    task_role: str | None = None,
) -> ComplexityTier:
    """Rule-based complexity tier, no LLM call.

    *role* is the model role; *task_role* is the RoleRouter task role, if known.
    Order: role override → DEEP (substring) → LIGHT (signal + short text, task role
    allowed, not about the agent) → STANDARD. DEEP is fail-safe as a plain substring
    match: a false positive only buys a stronger model.
    """
    if role in _ALWAYS_LIGHT_ROLES:
        return ComplexityTier.LIGHT

    if not isinstance(text, str):
        return ComplexityTier.STANDARD
    stripped = text.strip()
    if not stripped:
        return ComplexityTier.STANDARD

    normalized = stripped.casefold()

    for signal in _DEEP_SIGNALS:
        if signal in normalized:
            return ComplexityTier.DEEP

    if isinstance(task_role, str) and task_role in _NEVER_LIGHT_TASK_ROLES:
        return ComplexityTier.STANDARD

    # Вопрос о самом агенте не бывает лёгким, даже короткий и с бытовым сигналом.
    if asks_about_the_agent(normalized):
        return ComplexityTier.STANDARD

    is_short = len(stripped) < _SHORT_TEXT_THRESHOLD * 4  # ~180 chars
    if is_short:
        for _signal, pattern in _LIGHT_SIGNAL_PATTERNS:
            if pattern.search(normalized):
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
# Time-sensitive keywords → the planner adds a web_search step. Broad on purpose:
# a false positive costs one search, a false negative a stale answer.

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
    """Return True when the task likely requires fresh web data."""
    if not isinstance(text, str) or not text.strip():
        return False
    normalized = text.strip().casefold()
    return any(sig in normalized for sig in _LIVE_GROUNDING_SIGNALS)


# ── cheap-path planner gate ───────────────────────────────────────────────────
# A missed skip costs one planner call; a wrong skip could drop a needed tool step.
# So every gate below errs toward running the planner.

# Substrings hinting a tool may be needed; broad on purpose.
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

# Whole-word greeting/thanks tokens; a message qualifies only if EVERY word is here,
# so "hi, read the file" never does.
_GREETING_WORDS: frozenset[str] = frozenset({
    # EN
    "hello", "hi", "hey", "good", "morning", "evening", "afternoon",
    "thanks", "thank", "you", "greetings",
    # RU
    "привет", "здравствуй", "здравствуйте", "хай", "добрый", "доброе",
    "день", "вечер", "утро", "спасибо", "благодарю",
    # Social filler only, never content words (MIR-020).
    # EN filler
    "how", "are", "doing", "there", "all", "everyone", "night",
    "bye", "goodbye",
    # RU filler
    "как", "дела", "у", "тебя", "вас", "ты", "вы", "всё", "хорошо",
    "доброй", "ночи", "пока", "до", "свидания", "большое", "приветствую",
})

# Above this length an input is no longer "trivial chatter"; let the planner run.
_CHEAP_PATH_MAX_CHARS = 200

_WORD_RE = re.compile(r"[^\W\d_]+", re.UNICODE)


def _is_pure_greeting(normalized: str) -> bool:
    """True when *normalized* is at most 5 words, all from _GREETING_WORDS.

    Cap 5 fits «привет, как у тебя дела» (MIR-020).
    """
    words = _WORD_RE.findall(normalized)
    if not words or len(words) > 5:
        return False
    return all(w in _GREETING_WORDS for w in words)


def tool_signal_present(normalized: str) -> bool:
    """В реплике (уже в нижнем регистре) есть слово-признак инструмента.

    Общий список для `can_skip_planner` и `core/social_turn.py`.
    """
    return any(sig in normalized for sig in _TOOL_SIGNALS)


def can_skip_planner(text: str, *, file_hint: str | None = None) -> bool:
    """Return True when the planner LLM call can be safely skipped; never raises.

    Only short text with no file hint, tool signal, live-grounding need or DEEP tier,
    AND a ``key=value`` flag echo or pure greeting; "what is X" may need a tool.
    """
    if file_hint:
        return False
    if not isinstance(text, str):
        return False
    stripped = text.strip()
    if not stripped or len(stripped) > _CHEAP_PATH_MAX_CHARS:
        return False

    normalized = stripped.casefold()

    if tool_signal_present(normalized):
        return False
    if needs_live_grounding(stripped):
        return False
    if assess_complexity(stripped) is ComplexityTier.DEEP:
        return False

    if _CONFIG_FLAG_RE.match(stripped):
        return True
    return _is_pure_greeting(normalized)


