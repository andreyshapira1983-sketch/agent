"""Russian-to-English domain term table so a Russian question can match English records."""
from __future__ import annotations

import re

#: stem -> English terms it should also search for; keys are Russian stems matched as prefixes (inflection). Deterministic: no model call, no embeddings. Keep to terms the project uses.
_TERMS: dict[str, tuple[str, ...]] = {
    "агент": ("agent",),
    "архитектур": ("architecture",),
    "баг": ("bug",),
    "безопасн": ("security", "safety"),
    "бюджет": ("budget",),
    "ветк": ("branch",),
    "верификац": ("verification", "verifier"),
    "гигиен": ("hygiene",),
    "демон": ("daemon",),
    "доказательств": ("evidence", "proof"),
    "документ": ("document", "docs"),
    "доктрин": ("doctrine",),
    "дубл": ("duplicate",),
    "журнал": ("journal", "log"),
    "задач": ("task",),
    "инструмент": ("tool",),
    "коммит": ("commit",),
    "код": ("code",),
    "лог": ("log",),
    "маршрут": ("routing", "router"),
    "модел": ("model",),
    "обучен": ("learning", "learn"),
    "одобрен": ("approval",),
    "оператор": ("operator",),
    "откат": ("rollback",),
    "ошибк": ("error", "bug", "failure"),
    "памят": ("memory",),
    "патч": ("patch",),
    "план": ("plan", "planner"),
    "политик": ("policy",),
    "правил": ("rule", "policy"),
    "предложен": ("proposal",),
    "провер": ("check", "verify", "verification"),
    "процедур": ("procedure",),
    "репозитор": ("repository", "repo"),
    "риск": ("risk",),
    "роль": ("role",),
    "сборк": ("build",),
    "секрет": ("secret",),
    "слияни": ("merge",),
    "сет": ("network",),
    "тест": ("test", "tests", "pytest"),
    "улик": ("evidence",),
    "файл": ("file",),
    "цел": ("goal",),
    "эпизод": ("episode",),
}


_WORD_RE = re.compile(r"\w+", re.UNICODE)
_CYRILLIC_RE = re.compile(r"[а-яёА-ЯЁ]")
_LATIN_RE = re.compile(r"[a-zA-Z]")


def recall_language_diagnostics(question: str) -> dict[str, object]:
    """Whether a recall miss can be blamed on the language gap, or not."""
    text = question or ""
    has_cyrillic = bool(_CYRILLIC_RE.search(text))
    has_latin = bool(_LATIN_RE.search(text))
    if has_cyrillic and has_latin:
        script = "mixed"
    elif has_cyrillic:
        script = "cyrillic"
    else:
        script = "latin"
    tokens = {word.lower() for word in _WORD_RE.findall(text)}
    return {
        "question_script": script,
        "bilingual_terms_added": len(english_terms_for(tokens)),
    }


def english_terms_for(tokens: set[str]) -> set[str]:
    """English terms implied by the given (lowercased) question tokens; empty for a question with no Russian domain terms."""
    out: set[str] = set()
    for token in tokens:
        for stem, english in _TERMS.items():
            if token.startswith(stem):
                out.update(english)
    return out
