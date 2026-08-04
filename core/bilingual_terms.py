"""Russian question, English record — one domain vocabulary between them.

The operator writes in Russian; every repository artefact, persistent memory
included, is written in English. Retrieval scores by word overlap, so the two
never meet: measured on the live store, "кто владеет архитектурой?" recalled
0 records while "who owns the architecture?" recalled 3.

Deliberately a table, not a translator: no model call, no embeddings, so recall
stays deterministic and testable — the same contract the retrieval policy states
for itself. Keys are STEMS, matched as prefixes, because Russian inflects
("архитектура / архитектурой / архитектуры"). Keep entries to terms this project
actually uses; a general dictionary would make every question match everything.
"""
from __future__ import annotations

import re

#: stem -> English terms it should also search for.
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
    """Whether a recall miss can be blamed on the language gap, or not.

    Journalled on every miss so the decision about a bigger fix — translating
    the question, or semantic search — is made from counted misses instead of
    an impression. A miss on a Cyrillic question that this table could not
    widen at all (`bilingual_terms_added: 0`) is the case the table does not
    cover; a miss with terms added is a genuine "we do not know that".
    """
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
    """English terms the given (already lowercased) question tokens imply.

    Returns an empty set for a question with no Russian domain terms, so an
    English question is scored exactly as before.
    """
    out: set[str] = set()
    for token in tokens:
        for stem, english in _TERMS.items():
            if token.startswith(stem):
                out.update(english)
    return out
