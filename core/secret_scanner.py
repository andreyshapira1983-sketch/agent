"""Secret Scanner — single source of truth for credential detection (§7).

The architecture rule is: security belongs to the kernel, not to the LLM.
This module is that kernel. Every other component that needs to know
"is there a secret in this text?" must ask this module — not roll its own
patterns.

Used by:
  - MemoryWritePolicy           refuses to persist any record containing a hit
  - redaction.redact_text       masks hits inline before logs/prompts/output
  - DataClassifier              elevates classification to DataClass.SECRET
  - AgentLoop                   classifies tool outputs before they leave the loop

Two detection layers, intentionally separate:

  REGEX_RULES
    Specific, high-confidence credential shapes (OpenAI keys, GitHub PATs,
    AWS access keys, PEM blocks, `KEY=VALUE` style assignments, …).
    These have precise spans — they can be redacted inline.

  KEYWORD_RULES
    Soft alarms. The text *talks about* credentials (e.g. "password", an
    `Authorization:` header). The exact span of the secret value is
    ambiguous, so these mark the whole document as "contains secret",
    refuse memory writes, and bump classification — but do NOT drive
    inline redaction.

Adding a new pattern? Do it here, once. Every consumer picks it up for free.
"""
from __future__ import annotations

import re
from typing import Final

# Одна копия структуры находки — в core/dlp.py (заявка агента
# proposals/2026-09-21_dedupe_secretfinding_into_dlp.md; кампания 2026-09-22
# шесть раз подряд брала эту цель, а живой код менять не может). Имя
# SecretFinding остаётся импортируемым отсюда: его берёт core/redaction.py.
from core.dlp import DlpFinding as SecretFinding

# The set of KEY NAMES that make a value a credential. Single source of truth:
# it feeds both the flat-text rule (`credential-assignment`, below) and the
# structured-payload check (`is_credential_key`). Two hand-written copies would
# drift, and a drifting copy is how `{"password": ...}` leaked while
# `password: ...` was masked.
#
# Deliberately NOT a bare `token`: `max_tokens` is a model parameter, and
# redacting it would blind the logs this layer exists to keep readable.
CREDENTIAL_KEY_NAMES: Final[str] = (
    r"api[_-]?key|apikey|secret[_-]?key|password|passwd|passphrase|"
    r"auth[_-]?token|private[_-]?key|access[_-]?token"
)

# A dict key counts as credential-naming when it IS one of those names or ENDS
# with one after a separator: `openai_api_key` and `db_password` are the same
# secret wearing a prefix. `password_hint` is not — the suffix must be last.
_CREDENTIAL_KEY_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?i)^(?:.*[_.\-])?(?:{CREDENTIAL_KEY_NAMES})$"
)


def is_credential_key(name: str) -> bool:
    """True when a mapping key names a credential, so its value is a secret.

    Used for values that carry no signal of their own: an opaque password
    matches no regex, so the key is the only evidence available.
    """
    return isinstance(name, str) and bool(_CREDENTIAL_KEY_RE.match(name))


# Regex rules: (kind, compiled pattern). `kind` ends up in the redaction
# token, e.g. `[REDACTED:openai-key]`, so keep it short, lowercase, kebab.
REGEX_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Left boundary `(?<![A-Za-z0-9_-])` so "sk-" must START a token: without it
    # the hyphen-in-class + unanchored pattern matched INSIDE ordinary words
    # ("ta|sk-...", "ri|sk-...", "di|sk-...") + a hyphenated slug (CORE-13).
    ("anthropic-key",      re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key",         re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_\-]{20,}")),
    ("github-pat",         re.compile(r"(?<![A-Za-z0-9_-])ghp_[A-Za-z0-9]{20,}")),
    # GitHub issues five more token shapes besides the classic `ghp_` PAT, and
    # this repo drives `gh` — so any of them can surface in captured output.
    # Fine-grained PATs carry `_` inside the body, hence the wider class.
    ("github-fine-grained-pat", re.compile(
        r"(?<![A-Za-z0-9_-])github_pat_[A-Za-z0-9_]{20,}"
    )),
    # Body class is wider than the other GitHub rules: stateless installation
    # tokens are `ghs_<app id>_<JWT>`, so underscores and the JWT's dots and
    # dashes are part of the credential. Without them the rule matched only the
    # token's head and the redaction left the JWT tail readable (PR #207
    # review, CodeRabbit). Over-capture of a trailing sentence dot is the safe
    # direction for a redactor.
    ("github-token",       re.compile(r"(?<![A-Za-z0-9_-])gh[osur]_[A-Za-z0-9_.\-]{20,}")),
    # Slack bot/user/app tokens share the `xox<letter>-` prefix.
    ("slack-token",        re.compile(r"(?<![A-Za-z0-9_-])xox[abeprs]-[A-Za-z0-9-]{10,}")),
    # Incoming-webhook URLs are themselves the credential — no auth needed.
    # Same left boundary as every other rule: without it a match could start
    # inside a glued token ("...abchttps://..."), the one rule that differed.
    ("slack-webhook",      re.compile(
        r"(?<![A-Za-z0-9_-])https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}"
    )),
    # Google/Firebase API keys: fixed 39-char shape, distinctive enough alone.
    # Both ends anchored — the length is exact, so 40+ same-class characters
    # are some longer identifier that merely starts like a key, not a key.
    ("google-api-key",     re.compile(
        r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"
    )),
    ("gitlab-pat",         re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}")),
    ("npm-token",          re.compile(r"(?<![A-Za-z0-9_-])npm_[A-Za-z0-9]{20,}")),
    # Stripe uses `_` after the prefix, so the `sk-` rule above never sees it.
    # `sk` (secret) and `rk` (restricted) ONLY. `pk` (publishable) is public
    # by design and appears in frontend configs and build manifests; every hit
    # here feeds `contains_secret()` and the memory write policy, so flagging
    # it quarantined innocent content (PR #207 review, Greptile P1 +
    # CodeRabbit). `kk` was never a Stripe prefix — a leftover of the original
    # character class.
    ("stripe-key",         re.compile(
        r"(?<![A-Za-z0-9_-])[sr]k_(?:live|test)_[A-Za-z0-9]{20,}"
    )),
    ("huggingface-token",  re.compile(r"(?<![A-Za-z0-9_-])hf_[A-Za-z0-9]{20,}")),
    ("aws-access-key",     re.compile(r"AKIA[0-9A-Z]{16}")),
    # H-15 (Cloudbleed class, docs/audit/HISTORICAL_FAILURE_LEDGER.md).
    # Измерено 2026-08-24: `AKIA…` ловился, а СЕКРЕТ рядом с ним проходил
    # целиком — а публичен как раз идентификатор, опасна вторая половина.
    # Ловится ПОМЕЧЕННАЯ форма (конфиги, дампы окружения); голый
    # 40-символьный base64 без метки не ловится намеренно: он неотличим от
    # хеша, и правило по одной длине давало бы ложные срабатывания на
    # каждом sha и идентификаторе. Предел замерен и записан в журнале.
    ("aws-secret-key",     re.compile(
        r"(?i)aws[_-]?secret[_-]?(?:access[_-]?)?key[\"']?\s*[=:]\s*"
        r"[\"']?[A-Za-z0-9/+=]{40}"
    )),
    # Учётные данные ВНУТРИ адреса. Соседнее правило `mongodb-uri` знало
    # эту форму, но только для одной схемы; `https://user:pass@host`
    # не находился вовсе, хотя именно такие адреса агент и скачивает.
    ("url-credentials",    re.compile(
        r"(?:https?|ftp|ssh|redis|postgres(?:ql)?|mysql|amqp)://"
        r"[^:/?#\s@]+:[^@/?#\s]+@"
    )),
    ("bearer-token",       re.compile(r"Bearer\s+[A-Za-z0-9_.\-]{20,}")),
    # PEM block start marker alone is enough — pasting only the header is
    # already a leak. The END marker is optional in match.
    ("private-key-block",  re.compile(r"-----BEGIN (?:RSA |OPENSSH |EC )?PRIVATE KEY-----")),
    # JWT: three base64url-encoded segments separated by dots.
    # The first segment starts with eyJ (base64 of '{"') which is distinctive
    # enough to avoid false positives on version strings or file paths.
    ("jwt-token",          re.compile(
        r"eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}"
    )),
    # Telegram bot token: <bot_id>:<token>. Standard format issued by BotFather.
    ("telegram-bot-token", re.compile(r"\b\d{8,10}:AA[A-Za-z0-9_-]{33}\b")),
    # MongoDB/Atlas connection strings that embed a password.
    ("mongodb-uri",        re.compile(
        r"mongodb(?:\+srv)?://[^:/?#\s]+:[^@/?#\s]+@"
    )),
    # `KEY=VALUE` / `KEY: VALUE` shapes where KEY names a credential.
    # Matches the WHOLE assignment so the redactor can mask the value.
    ("credential-assignment", re.compile(
        rf"(?i)\b({CREDENTIAL_KEY_NAMES})\s*[:=]\s*\S+"
    )),
)


# Keyword rules: case-insensitive substring matches. These don't pinpoint a
# span — they flag the whole text as credential-adjacent. Order doesn't matter.
KEYWORD_RULES: Final[tuple[str, ...]] = (
    "api_key", "api-key", "apikey",
    "password", "passwd", "passphrase",
    "secret_key", "private_key",
    "authorization:", "auth_token",
)


def looks_like_secret_body(value: str) -> bool:
    """True, когда строка — ТЕЛО секрета (случайный материал), а не имя/путь/фраза.

    Авторство агента (2026-08-29). Меряется уникальность символов: настоящий
    ключ — шум без структуры, его символы взяты из широкого алфавита и почти
    не повторяются, поэтому доля уникальных высока. Имена, пути и фразы
    повторяют малый алфавит и несут словарные куски, и доля остаётся низкой.
    Узкому алфавиту (hex) нужен свой порог: даже длинная шестнадцатеричная
    строка повторяет всего 16 знаков и общей меры не достигнет никогда.

    Замерено на 12 формах (5 настоящих секретов, 7 законных строк, включая
    UUID и короткий git-sha) — 12/12. Первая мера того же автора (доля
    НЕ-словарных символов) была опровергнута замером: у реальных ключей она
    0.00–0.04, и правило пропустило бы их все.
    """
    s = (value or "").strip()
    if not s:
        return False
    if len(s) >= 40 and len(set(s)) / len(s) >= 0.55:
        return True
    return bool(len(s) >= 32 and re.fullmatch(r"[0-9a-fA-F]+", s))


#: Формы значения, которые НЕ являются секретом, а лишь указывают на него:
#: чтение из окружения, обращение к хранилищу, аннотация типа в сигнатуре,
#: ссылка на другую переменную. Правило узкое НАМЕРЕННО: пропустить настоящий
#: секрет дороже, чем лишний раз заблокировать (цена, названная агентом), —
#: поэтому здесь перечислены только явные ссылки, а всё остальное значение
#: по-прежнему считается телом.
_REFERENCE_VALUE_RE: Final[re.Pattern[str]] = re.compile(
    r"""^(?:
        (?:[A-Za-z_][\w.]*\s*[(\[])             # os.environ[...], getenv(...), cfg.get(
      | (?:str|int|float|bytes|bool|Optional|Any|Path)\b   # аннотация типа
      | \{\{|\$\{|<[A-Za-z_]                    # шаблонная подстановка
    )""",
    re.VERBOSE,
)


#: Служебные слова Python на месте «значения»: `if not api_key: return {...}` —
#: двоеточие закрывает условие, а не присваивает ключ. Ночь 24→25.09: сторож
#: отказал агенту в записи инструмента, который лишь проверял, задана ли
#: переменная окружения. Настоящий пароль словом языка не бывает.
_CODE_KEYWORD_VALUES: Final[frozenset[str]] = frozenset({
    "return", "raise", "pass", "continue", "break", "none", "true", "false",
})


def _assignment_is_only_a_reference(text: str, match: re.Match[str]) -> bool:
    """True, когда за именем доступа стоит ССЫЛКА, а не значение секрета.

    `credential-assignment` ловит форму «имя = значение» и не смотрит, что
    именно присвоено. Из-за этого блокировались обычный код (чтение значения
    из окружения) и объявление функции с параметром-именем: агент не мог
    записать ни план про доступы, ни код, работающий с ключами (замер
    2026-08-29). Обратное направление — короткий настоящий пароль вроде
    «password: hunter2» — обязано ловиться по-прежнему, поэтому пропускаются
    ТОЛЬКО явные ссылочные формы, а не «всё короткое».
    """
    raw = match.group(0)
    value = raw.split("=", 1)[-1] if "=" in raw else raw.split(":", 1)[-1]
    value = value.strip().strip("\"'` \t,;")
    if not value:
        return True
    if value.split(None, 1)[0].lower() in _CODE_KEYWORD_VALUES:
        return True
    if looks_like_secret_body(value):
        return False
    return bool(_REFERENCE_VALUE_RE.match(value))


def scan(text: str) -> list[SecretFinding]:
    """Return every regex hit in `text`. Empty list for clean input."""
    if not text:
        return []
    findings: list[SecretFinding] = []
    for kind, pat in REGEX_RULES:
        for m in pat.finditer(text):
            if kind == "credential-assignment" and _assignment_is_only_a_reference(text, m):
                # Присваивание-ссылка (окружение, аннотация, имя) — не утечка.
                continue
            findings.append(SecretFinding(kind=kind, start=m.start(), end=m.end(), matched=m.group(0)))
    return findings


def keyword_hits(text: str) -> list[str]:
    """Return the keywords found in `text` (lowercased)."""
    if not text:
        return []
    lower = text.lower()
    return [kw for kw in KEYWORD_RULES if kw in lower]


def contains_secret(text: str, *, include_keywords: bool = True) -> tuple[bool, list[str]]:
    """High-level check: does this text contain ANY secret signal?

    Returns (flag, reasons). Reasons follow the format the existing
    MemoryWritePolicy reasons used so existing audit consumers keep working:
      - "matches secret pattern '<kind>'"
      - "contains secret keyword '<kw>'"

    ``include_keywords`` controls the soft KEYWORD layer. When False, only
    the high-confidence REGEX rules (real credential *shapes* with a
    redactable span) count. Callers that scan content originating inside
    the trusted boundary — e.g. the agent's own logs, files and diffs —
    pass False so that merely *mentioning* a credential word (``api_key``,
    ``password``) does not mark their own evidence as a secret. Regex hits
    are never suppressed, so real leaked keys are still caught.
    """
    reasons: list[str] = []
    for f in scan(text):
        reasons.append(f"matches secret pattern '{f.kind}'")
    if include_keywords:
        for kw in keyword_hits(text):
            reasons.append(f"contains secret keyword '{kw}'")
    return bool(reasons), reasons
