"""Single source of truth for credential detection (kernel security, not LLM).

REGEX_RULES are precise credential shapes with redactable spans; KEYWORD_RULES are soft alarms that flag the whole text
but do not drive inline redaction. Consumers: MemoryWritePolicy, redaction.redact_text, DataClassifier, AgentLoop.
Add new patterns here, once.
"""
from __future__ import annotations

import re
from typing import Final

# The single copy of the finding structure lives in core/dlp.py; the name SecretFinding stays importable from here
# because core/redaction.py uses it.
from core.dlp import DlpFinding as SecretFinding

# KEY NAMES that make a value a credential; feeds both the flat-text rule (`credential-assignment`) and the
# structured-payload check (`is_credential_key`) so the two cannot drift.
# 
# Deliberately NOT a bare `token`: `max_tokens` is a model parameter, and redacting it would blind the logs.
CREDENTIAL_KEY_NAMES: Final[str] = (
    r"api[_-]?key|apikey|secret[_-]?key|password|passwd|passphrase|"
    r"auth[_-]?token|private[_-]?key|access[_-]?token"
)

# A dict key counts as credential-naming when it ENDS with one of those names:
# `openai_api_key`, `dbPassword` and `DBPASSWORD` are the same secret wearing a
# prefix. `password_hint` is not — the suffix must be last.
_CREDENTIAL_KEY_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?i)(?:{CREDENTIAL_KEY_NAMES})$"
)


def is_credential_key(name: str) -> bool:
    """True when a mapping key names a credential, so its value is a secret.

    Used for values that carry no signal of their own: an opaque password
    matches no regex, so the key is the only evidence available.
    """
    return isinstance(name, str) and bool(_CREDENTIAL_KEY_RE.search(name))


#: PEM label of any private key: `RSA PRIVATE KEY`, `ENCRYPTED PRIVATE KEY`, …
_PEM_LABEL: Final[str] = r"(?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?"
_PEM_NL: Final[str] = r"(?:\r?\n|\\r\\n|\\n)"
#: Without an END line: armor headers (`Version: …`), blank lines, base64 lines.
_PEM_OPEN_BODY: Final[str] = (
    rf"(?:{_PEM_NL}[ \t]*[A-Za-z][A-Za-z0-9-]*:[ \t][^\r\n\\]*)*"
    rf"(?:{_PEM_NL}[ \t]*(?={_PEM_NL}))*"
    rf"(?:{_PEM_NL}[ \t]*[A-Za-z0-9+/=]+[ \t]*(?=\r?\n|\\r|\\n|$))*"
)

#: A quoted value on one line, escapes allowed; then the `\"…\"` of JSON in a string.
_QUOTED_VALUE: Final[str] = (
    r"""(?:"(?:[^"\\\n]|\\.)*"|'(?:[^'\\\n]|\\.)*'|\\"[^"\n]*"|\\'[^'\n]*')"""
)


# Regex rules: (kind, compiled pattern). `kind` ends up in the redaction token, e.g. `[REDACTED:openai-key]`:
# keep it short, lowercase, kebab.
REGEX_RULES: Final[tuple[tuple[str, re.Pattern[str]], ...]] = (
    # Left boundary `(?<![A-Za-z0-9_-])` so "sk-" must START a token; without it the pattern matched inside ordinary
    # words ("ta|sk-...", "ri|sk-...") and hyphenated slugs.
    ("anthropic-key",      re.compile(r"(?<![A-Za-z0-9_-])sk-ant-[A-Za-z0-9_\-]{20,}")),
    ("openai-key",         re.compile(r"(?<![A-Za-z0-9_-])sk-[A-Za-z0-9_\-]{20,}")),
    ("github-pat",         re.compile(r"(?<![A-Za-z0-9_-])ghp_[A-Za-z0-9]{20,}")),
    # GitHub issues five more token shapes besides the classic `ghp_` PAT, and this repo drives `gh`, so any of them can
    # surface in captured output. Fine-grained PATs carry `_` inside the body, hence the wider class.
    ("github-fine-grained-pat", re.compile(
        r"(?<![A-Za-z0-9_-])github_pat_[A-Za-z0-9_]{20,}"
    )),
    # Body class is wider than the other GitHub rules: stateless installation tokens are `ghs_<app id>_<JWT>`, so
    # underscores and the JWT's dots and dashes belong to the credential. Over-capturing a trailing sentence dot is the
    # safe direction for a redactor.
    ("github-token",       re.compile(r"(?<![A-Za-z0-9_-])gh[osur]_[A-Za-z0-9_.\-]{20,}")),
    # Slack bot/user/app tokens share the `xox<letter>-` prefix.
    ("slack-token",        re.compile(r"(?<![A-Za-z0-9_-])xox[abeprs]-[A-Za-z0-9-]{10,}")),
    # Incoming-webhook URLs are themselves the credential. Same left boundary as every other rule (a match could
    # otherwise start inside a glued token).
    ("slack-webhook",      re.compile(
        r"(?<![A-Za-z0-9_-])https://hooks\.slack\.com/services/[A-Za-z0-9/_-]{20,}"
    )),
    # Google/Firebase API keys: fixed 39-char shape. Both ends anchored: 40+ same-class characters are a longer
    # identifier that merely starts like a key.
    ("google-api-key",     re.compile(
        r"(?<![A-Za-z0-9_-])AIza[0-9A-Za-z_-]{35}(?![0-9A-Za-z_-])"
    )),
    ("gitlab-pat",         re.compile(r"(?<![A-Za-z0-9_-])glpat-[A-Za-z0-9_-]{20,}")),
    ("npm-token",          re.compile(r"(?<![A-Za-z0-9_-])npm_[A-Za-z0-9]{20,}")),
    # Stripe uses `_` after the prefix, so the `sk-` rule never sees it. `sk` (secret) and `rk` (restricted) ONLY:
    # `pk` (publishable) is public by design and appears in frontend configs, and every hit here feeds
    # `contains_secret()` and the memory write policy.
    ("stripe-key",         re.compile(
        r"(?<![A-Za-z0-9_-])[sr]k_(?:live|test)_[A-Za-z0-9]{20,}"
    )),
    ("huggingface-token",  re.compile(r"(?<![A-Za-z0-9_-])hf_[A-Za-z0-9]{20,}")),
    ("aws-access-key",     re.compile(r"AKIA[0-9A-Z]{16}")),
    # Cloudbleed class (H-15). AWS secret access key next to an `AKIA...` id: the id is public, the second half is the
    # danger. Only the LABELLED form (configs, environment dumps) is caught; a bare 40-char base64 is indistinguishable
    # from a hash, and a length-only rule would fire on every sha and identifier.
    ("aws-secret-key",     re.compile(
        r"(?i)aws[_-]?secret[_-]?(?:access[_-]?)?key[\"']?\s*[=:]\s*"
        r"[\"']?[A-Za-z0-9/+=]{40}"
    )),
    # Credentials INSIDE a URL (`https://user:pass@host`); the neighbouring `mongodb-uri` rule covers only one scheme.
    ("url-credentials",    re.compile(
        r"(?:https?|ftp|ssh|redis|postgres(?:ql)?|mysql|amqp)://"
        r"[^:/?#\s@]+:[^@/?#\s]+@"
    )),
    ("bearer-token",       re.compile(r"Bearer\s+[A-Za-z0-9_.\-]{20,}")),
    # The whole block, BEGIN to END; without an END line, the body lines after
    # the header. A bare header is still a finding. Newlines real or escaped.
    ("private-key-block",  re.compile(
        rf"-----BEGIN {_PEM_LABEL}-----"
        rf"(?:[\s\S]{{0,16000}}?-----END {_PEM_LABEL}-----|{_PEM_OPEN_BODY})"
    )),
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
    # No left edge: the name may be glued to a prefix (`dbPassword`). A quoted
    # value is taken whole. A QUOTED key (`"password": "…"`, JSON, also escaped)
    # needs a quoted value — `{"api_key": api_key}` is code passing a variable.
    ("credential-assignment", re.compile(
        rf"(?i)({CREDENTIAL_KEY_NAMES})"
        rf"(?:\s*[:=]\s*(?P<value>{_QUOTED_VALUE}|\S+)"
        rf"|\\?[\"']\s*[:=]\s*(?P<qvalue>{_QUOTED_VALUE}))"
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
    """True when the string is a secret BODY (random material), not a name, path or phrase.

        Measures character uniqueness: a real key is structureless noise from a wide alphabet, so its share of unique
        characters is high; names, paths and phrases reuse a small alphabet. A narrow alphabet (hex) needs its own
        threshold: even a long hex string repeats only 16 symbols and never reaches the general one.
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

#: Flag values that clear a PREFIXED name only (`require_password: yes`).
_FLAG_VALUES: Final[frozenset[str]] = frozenset({"yes", "no", "on", "off", "0", "1"})


def _assignment_is_only_a_reference(text: str, match: re.Match[str]) -> bool:
    """True when the credential name is followed by a REFERENCE, not a secret value.

        `credential-assignment` matches "name = value" without looking at what is assigned, which blocked ordinary code
        (reading from the environment) and function declarations with a name parameter. Only explicit reference forms
        are skipped, not "anything short": a short real password such as "password: hunter2" must still be caught.
    """
    value = match.group("value") or match.group("qvalue") or ""
    value = value.strip().strip("\"'` \t,;\\")
    if not value:
        return True
    if value.split(None, 1)[0].lower() in _CODE_KEYWORD_VALUES:
        return True
    before = text[max(match.start() - 1, 0) : match.start()]
    if value.lower() in _FLAG_VALUES and (before.isalnum() or before == "_"):
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
                # A reference assignment (environment, annotation, name) is not a leak.
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
    """High-level check: does this text contain ANY secret signal? Returns (flag, reasons).

        Reasons keep the format MemoryWritePolicy used ("matches secret pattern '<kind>'", "contains secret keyword
        '<kw>'") so audit consumers keep working. ``include_keywords=False`` counts only high-confidence REGEX shapes:
        callers scanning content from inside the trusted boundary (own logs, files, diffs) pass it so that merely
        mentioning a credential word does not mark their own evidence as secret. Regex hits are never suppressed.
    """
    reasons: list[str] = []
    for f in scan(text):
        reasons.append(f"matches secret pattern '{f.kind}'")
    if include_keywords:
        for kw in keyword_hits(text):
            reasons.append(f"contains secret keyword '{kw}'")
    return bool(reasons), reasons
