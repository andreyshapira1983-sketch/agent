"""Helpers extracted verbatim from ``core/best_next_action.py`` by the incremental
splitter. The original module re-exports every name below, so all
existing import paths keep working."""

from __future__ import annotations

import re
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "none"]

_P_DAEMON_DOWN = 100      # heartbeat missing/stale: agent may not be running

_P_TICK_ERROR = 90        # last tick raised: the loop itself is broken

_P_TESTS_FAIL = 80        # concrete failing tests: minimal repair is provable

_P_TESTS_INCONCLUSIVE = 60  # timed-out/unknown: must not be read as healthy

_P_ENGINEERING_TASK = 59  # the campaign goal asks for engineering work: the

_P_CHARTER_DOCUMENT = 58  # the campaign goal itself asks for a doctrine draft:

_P_EXTERNAL_STUDY = 57    # the goal asks to STUDY the outside world: above the

_P_OWN_ISSUE_NAMED = 59   # the goal names one of the agent's OWN registered

_P_SELF_IMPROVEMENT_FAILURE = 55  # recent rollback/rejection despite clean health

_P_SELF_IMPROVEMENT_FAILURE_FRESH = 60  # a failure younger than a day is a

_P_INBOX_DEBT = 50        # duplicate proposals accumulating into admin debt

_P_CAUSAL_EXPERIMENT = 47  # claims with experiment specs: the intervention

_P_CAUSAL_DISCRIMINATE = 46  # claims with live probes: finishing an open

_P_CAUSAL_CLIMB = 45      # unexplained self-failure observations: investigate

_P_SPEC_BIRTH = 44      # marked claims without specs: the judge is exhausted

_P_DRY_RUN_STUCK = 40     # many dry-run ticks: never applied anything, ask why

_P_INBOX_BACKLOG = 30     # large pending queue with no clear duplicates

_P_OBSERVE = 0            # nothing pressing: stay in honest observation

_DRY_RUN_STREAK_ALERT = 5     # ~5 consecutive dry-run ticks before nudging

_INBOX_DEBT_DUPLICATES = 3    # this many duplicates is real debt, not noise

_INBOX_BACKLOG_PENDING = 12   # backlog worth a dedicated review pass

_SUPPRESSIBLE_SEVERITIES = frozenset({"medium", "low"})

_SUPPRESSIBLE_ACTIONS = frozenset({
    "reduce_inbox_duplicate_debt",  # medium
    "review_dry_run_stall",         # medium
    "review_inbox_backlog",         # low
})

def is_suppressible_alert(action: str) -> bool:
    """Whether an alert with this action name may be acknowledged. Pure."""
    return str(action) in _SUPPRESSIBLE_ACTIONS

_DRAFT_VERB_RE = re.compile(
    r"\b(draft|write|compose|напиш|черновик|состав)", re.IGNORECASE
)

_DOC_NAME_RE = re.compile(r"[\w/.\-]+\.md\b")

def doc_target_from_goal(goal: str) -> str:
    """Repo-путь документа, который цель просит написать, или "".

    Явный путь в цели сохраняется; голое имя едет в knowledge/doctrine/future/
    — дом целевых (ещё не действующих) документов доктрины.
    """
    text = str(goal or "")
    if not _DRAFT_VERB_RE.search(text):
        return ""
    match = _DOC_NAME_RE.search(text)
    if not match:
        return ""
    name = match.group(0).strip("'\"")
    if "/" in name:
        return name
    return f"knowledge/doctrine/future/{name}"

_STUDY_VERB_RE = re.compile(r"\b(изучи|прочитай|почитай|посмотри|study|read|research)",
                            re.IGNORECASE)

_OUTSIDE_RE = re.compile(r"интернет|сайт|http|www\.|в вебе|\bweb\b", re.IGNORECASE)

_ENGINEERING_GOAL_RE = re.compile(
    r"(?i)backlog|бэклог|self-build|failing.?test|падающ\w+ тест|proven gap"
    r"|доказанн\w+ разрыв|раскол|module.?split|split (?:of|proposal|plan)"
    r"|engineering candidate|инженерн\w+ кандидат|разбиени\w+ модул",
)

_PY_TARGET_RE = re.compile(r"\b[\w/\\.-]+\.py\b")

_ENGINEERING_CONTEXT_RE = re.compile(
    r"(?i)split|refactor|restructur|decompos|модул|module|раздели|разбей|почини|fix",
)

def _is_engineering_goal(text: str) -> bool:
    if _ENGINEERING_GOAL_RE.search(text):
        return True
    return bool(_PY_TARGET_RE.search(text) and _ENGINEERING_CONTEXT_RE.search(text))

_SUBJECT_TOKEN_RE = re.compile(r"[\w.-]+(?:[/\\][\w.-]+)+")

_COMMAND_TOKEN_RE = re.compile(r":[a-z][a-z0-9-]*")

def resolve_goal_subject(text: str, *, exists, command_module=None) -> str | None:
    """Артефакт, О КОТОРОМ цель, — по существованию, а не по суффиксу.

    Два словаря, потому что агент пишет обоими: путь к файлу и имя команды.
    Читать один означало объявлять «предмет не назван» там, где он назван точно
    (MIR-174). `exists` и `command_module` внедряются, чтобы решающая таблица
    осталась чистой функцией. Замер и границы: MIR-158, MIR-174.
    """
    for raw in _SUBJECT_TOKEN_RE.findall(str(text or "")):
        token = raw.replace("\\", "/").strip("`'\",.;:()[]")
        for candidate in (token, f"{token}.py"):
            if candidate and exists(candidate):
                return candidate
    if command_module is not None:
        for raw in _COMMAND_TOKEN_RE.findall(str(text or "")):
            module = command_module(raw)
            if module and exists(module):
                return module
    return None

def _abs_path_exists(path: str) -> bool:
    from pathlib import Path

    return Path(path).exists()


def unresolved_goal_targets(text: str, *, exists, exists_abs=_abs_path_exists) -> tuple[str, ...]:
    """Пути, названные целью и не существующие ни в рабочей области, ни на диске (MIR-161).

    Путь с «/» в начале проверяется на диске как есть (прогон 26.09: модели в /root/models)."""
    source = str(text or "")
    out: list[str] = []
    for match in _SUBJECT_TOKEN_RE.finditer(source):
        token = match.group(0).replace("\\", "/").strip("`'\",.;:()[]")
        if not token or exists(token) or exists(f"{token}.py"):
            continue
        if match.start() > 0 and source[match.start() - 1] == "/" and exists_abs("/" + token):
            continue
        out.append(token)
    return tuple(dict.fromkeys(out))

def _named_target(text: str) -> str | None:
    """Файл, названный в тексте цели, если он там назван.

    Отдельная функция, а не второй разбор внутри решения: тот же `_PY_TARGET_RE`,
    которым цель признаётся инженерной, отдаёт СОВПАДЕНИЕ, а не только «да».
    Решение, принятое ПОТОМУ ЧТО файл назван, обязано этот файл унести.

    Совпадение приводится к одному написанию разделителя, и это не косметика.
    `_PY_TARGET_RE` обратный слэш ПРОПУСКАЕТ, а результат отсюда уходит сразу в
    два места: в `target_path` (`core/best_next_action.py`) и в ключ разрешения
    (`core.autonomous_runtime._effects_dedup_key`). Сырое совпадение ломало оба
    — названный целью файл отвергался по несовпадению строк, а один и тот же
    файл, написанный через `\\`, заводил ОТДЕЛЬНУЮ заявку на одобрение (ревизии
    PR #342 и #343; замер — три разных ключа на пять написаний одного файла).
    Тот же `.replace("\\\\", "/")` давно стоит в соседнем `resolve_goal_subject`:
    в тексте цели обратный слэш — разделитель, а не имя.

    Регистр здесь НЕ трогается: по этому пути потом открывают файл, а на POSIX
    регистр значим. Кому нужна свёртка — складывает у себя.
    """
    match = _PY_TARGET_RE.search(text or "")
    if match is None:
        return None
    found = match.group(0).replace("\\", "/")
    while found.startswith("./"):
        found = found[2:]
    return found or None

_GOAL_WORD_RE = re.compile(r"[A-Za-zА-Яа-яЁё_][\w'-]{3,}")

_GOAL_STOP_WORDS = frozenset({
    "that", "this", "with", "from", "into", "then", "than", "when", "what",
    "which", "where", "while", "about", "after", "before", "their", "there",
    "them", "have", "been", "were", "will", "your", "each", "only", "also",
    "draft", "reviewed", "proposal", "read", "run", "make", "add",
})

_OWN_ISSUE_MIN_SHARED_WORDS = 3

def _goal_names_issue(goal: str, issue: dict) -> bool:
    """Называет ли цель ЭТУ запись реестра: отпечаток, действие, файл — буквально;
    заголовок — тремя содержательными словами."""
    text = str(goal or "")
    low = text.lower()
    if not low.strip():
        return False
    for literal in (
        str(issue.get("fingerprint") or ""),
        str(issue.get("action") or ""),
        *[str(f) for f in issue.get("related_files") or ()],
    ):
        if literal and literal.lower() in low:
            return True
    goal_words = {
        w.lower() for w in _GOAL_WORD_RE.findall(text)
    } - _GOAL_STOP_WORDS
    title_words = {
        w.lower() for w in _GOAL_WORD_RE.findall(str(issue.get("title") or ""))
    } - _GOAL_STOP_WORDS
    return len(goal_words & title_words) >= _OWN_ISSUE_MIN_SHARED_WORDS

_GOAL_ID_RE = re.compile(r"\b[A-Za-z]+[_-][\w-]*?[0-9a-f]{6,}\b")
