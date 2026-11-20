"""Helpers split out of ``core/best_next_action.py``; the original module re-exports every name."""

from __future__ import annotations

import re
from typing import Literal

Severity = Literal["critical", "high", "medium", "low", "none"]

_P_DAEMON_DOWN = 100      # heartbeat missing/stale: agent may not be running

_P_TICK_ERROR = 90        # last tick raised: the loop itself is broken

_P_TESTS_FAIL = 80        # concrete failing tests: minimal repair is provable

_P_TESTS_INCONCLUSIVE = 60  # timed-out/unknown: must not be read as healthy

_P_ENGINEERING_TASK = 59  # the campaign goal asks for engineering work

_P_CHARTER_DOCUMENT = 58  # the campaign goal itself asks for a doctrine draft

_P_EXTERNAL_STUDY = 57    # the goal asks to study the outside world

_P_OWN_ISSUE_NAMED = 59   # the goal names one of the agent's own registered issues

_P_SELF_IMPROVEMENT_FAILURE = 55  # recent rollback/rejection despite clean health

_P_SELF_IMPROVEMENT_FAILURE_FRESH = 60  # a failure younger than a day

_P_INBOX_DEBT = 50        # duplicate proposals accumulating into admin debt

_P_CAUSAL_EXPERIMENT = 47  # claims with experiment specs

_P_CAUSAL_DISCRIMINATE = 46  # claims with live probes

_P_CAUSAL_CLIMB = 45      # unexplained self-failure observations: investigate

_P_SPEC_BIRTH = 44      # marked claims without specs

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
    """Repo path of the document the goal asks to write, or "".

    An explicit path is kept; a bare name goes to knowledge/doctrine/future/.
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
    """The artifact the goal is ABOUT, decided by existence, not by suffix.

    Checks both file paths and command names; `exists` and `command_module` are injected to keep the table pure.
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
    """Paths named by the goal that exist neither in the workspace nor on disk.

    A leading "/" path is checked on disk as written."""
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
    """The file named in the goal text, if any, with backslashes normalised to "/".

    The `_PY_TARGET_RE` match is reused so a decision made because a file is named carries that file.
    Case is left untouched: the path is opened later and case matters on POSIX.
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
    """Whether the goal names THIS registry entry: fingerprint, action or file literally; title by three content words."""
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
