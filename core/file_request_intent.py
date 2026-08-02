"""What kind of file request is this question? Deterministic classifiers.

Moved out of ``core/loop.py`` (2026-08-02, piece 2 of its decomposition).
Every function here answers one routing question the loop asks about the
operator's wording before any tool runs:

* does the question explicitly ask to read the hinted file?
* is it a review/reading request at all?
* is it a CHANGE request — work to produce, run or record — which a reading
  must never swallow (the defect PR #211/#213 fixed, and the guard that
  keeps it fixed lives here)?
* did the operator explicitly name multi-file mode, or merely look like it?
* is a user-supplied path safe to read from the workspace?

No LLM, no I/O except the filesystem checks in ``validate_user_file_path``,
no state. The judgement-by-model counterpart is ``core/intent_understanding``;
choosing WHAT to analyse among candidates is ``core/referent_resolver``. This
module only classifies the request's wording and validates its paths — it
deliberately knows nothing about the loop.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from tools.file_read import MAX_BYTES as FILE_READ_MAX_BYTES


def explicitly_requests_hinted_file(question: str) -> bool:
    text = " ".join(question.casefold().split())
    return any(
        phrase in text
        for phrase in (
            "прочитай файл",
            "прочти файл",
            "прочитать файл",
            "проанализируй файл",
            "анализ файла",
            "файл задания",
            "task file",
            "read the file",
            "read this file",
            "analyze the file",
        )
    )


def file_hint_source(file_hint: str) -> dict[str, Any]:
    return {
        "tool": "file_read",
        "arguments": {"path": file_hint},
        "label": f"file:{file_hint}",
        "expected_outcome": "Non-empty UTF-8 text from the hinted file.",
    }


def is_file_review_request(question: str) -> bool:
    text = " ".join(question.casefold().split())
    return any(
        term in text
        for term in (
            "review",
            "read",
            "compare",
            "check",
            "проверь",
            "проверить",
            "прочитай",
            "прочти",
            "прочитать",
            "сравни",
            "сравнить",
            "проанализируй",
            "анализ",
        )
    )


# Verbs stating an intent to produce, run or record — the thing a reading
# never needs. Matched on word boundaries, because the defect this guard
# exists to fix is a substring scan: "implement" must not fire inside
# "implementation" and "commit" must not fire inside "commits", or a review
# OF how something is implemented stops being a review. Russian forms spell
# the endings they accept rather than trailing `\w*`, for the same reason.
_CHANGE_INTENT_RE = re.compile(
    r"(?<!\w)(?:"
    # produce or modify
    r"созда(?:й|йте|ть)|извлек(?:и|ите)|извлечь|напиш(?:и|ите)|написать|"
    r"перенес(?:и|ите|ти)|переименуй(?:те)?|переименовать|"
    r"удал(?:и|ите|ить)|"
    r"исправ(?:ь|ьте|ить)|почини(?:те)?|починить|реализ(?:уй|уйте|овать)|"
    r"отрефактор(?:и|ить)|"
    r"create|extract|implement|refactor|rewrite|rename|delete|write|"
    # run or record
    r"запусти(?:те)?|запустить|закоммить|коммит|запиш(?:и|ите)|записать|"
    r"commit|run\s+(?:the\s+)?tests?"
    r")(?!\w)",
    re.IGNORECASE,
)

# Punctuation a file name picks up around it: sentence and list marks, and
# the quoting a path is usually written in — backticks included, since a
# question about `commit.log` arrives fenced far more often than bare.
_TOKEN_EDGE_PUNCT = ".,;:!?()[]{}<>«»\"'`"


def strip_file_tokens(text: str) -> str:
    """Drop whitespace-separated tokens that name a file.

    Deliberately not a regular expression. The first version was one, and
    CodeQL was right about it: `[\\w./\\\\-]*[\\w-]\\.` lets the leading
    class and the character after it match the same input, so the engine
    re-splits a long run of dashes at every position — and the text here is
    the user's question, so the input is attacker-shaped by definition.
    Measured on 16 000 dashes: that pattern 2 019 ms, a segmented rewrite
    3 320 ms (worse), this loop 0.0 ms.

    Extension-agnostic on purpose: `_extract_path_mentions` knows seven
    extensions, so `commit.log` and `commit.ts` would otherwise stay in the
    text and vote for "commit". The suffix must be ASCII alphanumeric,
    which keeps prose out — a sentence ending in "коммит." has nothing
    after the dot, and "и т.д." is Cyrillic.
    """
    kept: list[str] = []
    for token in text.split():
        bare = token.strip(_TOKEN_EDGE_PUNCT)
        head, dot, extension = bare.rpartition(".")
        if (
            dot
            and head
            and 1 <= len(extension) <= 8
            and extension.isascii()
            and extension.isalnum()
        ):
            continue
        kept.append(token)
    return " ".join(kept)


def is_change_request(question: str) -> bool:
    """True when the request asks for work, not for a reading.

    Unambiguous verbs only. A review request may perfectly well contain
    "проверь" or "check", and claiming those would disable the reading path
    this guard exists to protect.

    Path mentions are removed before matching: they are what the request is
    ABOUT, and a file called `commit.md` or `branch.md` must not be read as
    an instruction to commit. Left in, the guard would repeat in miniature
    the defect it fixes — deciding intent from text that is not about
    intent.

    Removal is one linear pass over the tokens. It used to also substitute
    each path `_extract_path_mentions` returned, which meant one full scan
    of the question per path — quadratic again, on the same
    attacker-controlled text, and redundant: every path that extractor can
    return carries an extension, so the token pass already removes it, in
    any casing and without knowing the extension allowlist.
    """
    return bool(_CHANGE_INTENT_RE.search(strip_file_tokens(question)))


def is_explicit_multi_file_mode(question: str) -> bool:
    text = " ".join(question.casefold().split())
    return any(
        term in text
        for term in (
            "explicit multi-file",
            "multi-file review",
            "multi file review",
            "multi-file read",
            "multi file read",
            "режим нескольких файлов",
            "несколько файлов",
            "многофайлов",
        )
    )


def looks_like_multi_file_review_without_hint(question: str) -> bool:
    text = " ".join(question.casefold().split())
    return any(
        term in text
        for term in (
            "сравни",
            "сравнить",
            "compare",
            "review these files",
            "read these files",
        )
    )


def validate_user_file_path(raw_path: str, *, workspace: Path) -> dict[str, Any]:
    cleaned = raw_path.strip().strip("\"'")
    normalized = cleaned.replace("\\", "/")
    if re.match(r"^[A-Za-z]:/", normalized):
        return {"ok": False, "reason": "absolute path escapes workspace"}
    path = Path(normalized)
    if any(part == ".." for part in path.parts):
        return {"ok": False, "reason": "path traversal is not allowed"}
    target = path.resolve() if path.is_absolute() else (workspace / path).resolve()
    try:
        target.relative_to(workspace)
    except ValueError:
        return {"ok": False, "reason": "absolute path escapes workspace"}
    if not target.exists():
        return {"ok": False, "reason": "missing file"}
    if target.is_dir():
        return {"ok": False, "reason": "directories require explicit ingestion command"}
    if not target.is_file():
        return {"ok": False, "reason": "not a regular file"}
    size = target.stat().st_size
    if size > FILE_READ_MAX_BYTES:
        return {
            "ok": False,
            "reason": f"file too large ({size} bytes > {FILE_READ_MAX_BYTES})",
        }
    rel_path = target.relative_to(workspace)
    return {
        "ok": True,
        "target": target,
        "relative_path": rel_path,
    }
