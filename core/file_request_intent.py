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
from typing import Any, Callable

from core.planner import PlannerOutput
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
# (Named for the file-name edges it strips; the earlier `_TOKEN_…` name
# tripped secret scanners, which read "token" as a credential.)
_FILENAME_EDGE_PUNCT = ".,;:!?()[]{}<>«»\"'`"


def strip_file_tokens(text: str) -> str:
    """Drop whitespace-separated tokens that name a file.

    Deliberately not a regular expression. The first version was one, and
    CodeQL was right about it: `[\\w./\\\\-]*[\\w-]\\.` lets the leading
    class and the character after it match the same input, so the engine
    re-splits a long run of dashes at every position — and the text here is
    the user's question, so the input is attacker-shaped by definition.
    Measured on 16 000 dashes: that pattern 2 019 ms, a segmented rewrite
    3 320 ms (worse), this loop 0.0 ms.

    Extension-agnostic on purpose: `extract_path_mentions` knows seven
    extensions, so `commit.log` and `commit.ts` would otherwise stay in the
    text and vote for "commit". The suffix must be ASCII alphanumeric,
    which keeps prose out — a sentence ending in "коммит." has nothing
    after the dot, and "и т.д." is Cyrillic.
    """
    kept: list[str] = []
    for token in text.split():
        bare = token.strip(_FILENAME_EDGE_PUNCT)
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
    each path `extract_path_mentions` returned, which meant one full scan
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


# ---------------------------------------------------------------------------
# extract_path_mentions — a linear scanner, replacing a quadratic regex
# ---------------------------------------------------------------------------
#
# The regex this replaces carried the lookbehind guard from commit 113bd88,
# which killed the separator-wall cost the team measured (4.7s -> 2ms). CodeQL
# kept flagging it (alert #11, previously #6), and on re-measurement CodeQL was
# right: the guard blocks starts after `/.-` but not after letters, so a wall
# of CLASS characters still offered O(n) starts, each rescanning the rest.
# Measured on this machine before this rewrite: "a."*8000 -> 2.79s,
# "a"*16000 -> 2.80s, "-a"*8000 -> 1.39s. After: all under 5ms.
#
# The scanner walks the text once. Equivalence with the old regex is not
# asserted by argument but by tests: the original pattern lives verbatim in
# tests/test_file_request_intent_paths.py as the oracle, and the fuzz corpus
# there includes the adversarial shapes above plus every .py/.md file of this
# repository.

_PATH_CLS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_PATH_SLASH = frozenset("/\\")
#: The old lookbehind `(?<![/.\-])`: a match may not start right after one of
#: these. Backslash is deliberately absent — `.\main.py` is a path an operator
#: on Windows writes (see commit 113bd88).
_PATH_GUARD = frozenset("/.-")
#: Alternation order preserved from the regex: at the same dot, `py` wins over
#: `pdf` because it is tried first.
_PATH_EXTS = ("py", "md", "txt", "json", "yml", "yaml", "pdf")


def _ext_at(text: str, dot: int) -> int:
    """Length of the extension right after ``text[dot]``, or 0."""
    for ext in _PATH_EXTS:
        if text[dot + 1: dot + 1 + len(ext)].lower() == ext:
            return len(ext)
    return 0


def _drive_body_start(text: str, letter: int) -> int:
    """Body start after a drive prefix at ``text[letter]``, or -1.

    The grammar is ``[A-Za-z]:[\\/]`` followed by the OPTIONAL bare ``/`` —
    so ``C://a.py`` and ``https://www.py``'s tail ``s://www.py`` both parse.
    One helper for both the candidate branch and the failed-scan recheck,
    because the first version fixed the double slash in one of them only and
    the oracle fuzz caught the other within a thousand cases.
    """
    n = len(text)
    ch = text[letter]
    if not (ch.isascii() and ch.isalpha()):
        return -1
    if not (letter + 2 < n and text[letter + 1] == ":" and text[letter + 2] in _PATH_SLASH):
        return -1
    after = letter + 3
    if after < n and text[after] == "/" and after + 1 < n and text[after + 1] in _PATH_CLS:
        after += 1
    if after < n and text[after] in _PATH_CLS:
        return after
    return -1


def _scan_body(text: str, start: int) -> tuple[int, int]:
    """``(match_end, scan_stop)`` for the path body beginning at ``start``.

    ``match_end`` is -1 when no match exists; ``scan_stop`` is the index of
    the terminator that ended the walk — the caller may only skip THAT far
    on failure, because the region beyond it was never examined (a ``//``
    or a slash-then-backslash breaks the body but not the text).

    The body is class-runs joined by single slashes. Mirrors the regex's
    greedy star + backtracking final: the star consumed whole runs (the
    segment class excludes slashes, so partial-run splits could never
    succeed), and the final segment settled on the RIGHTMOST dot whose
    extension matched. So: collect the runs, then search them last-to-first
    for the rightmost dot with a known extension and a non-empty head.
    """
    n = len(text)
    runs: list[tuple[int, int]] = []
    i = start
    while i < n and text[i] in _PATH_CLS:
        run_start = i
        while i < n and text[i] in _PATH_CLS:
            i += 1
        runs.append((run_start, i))
        # A single slash joins the next run; a double slash (or a slash at
        # the end) terminates the body, exactly as `[cls]+[\/]` did.
        if i < n and text[i] in _PATH_SLASH and i + 1 < n and text[i + 1] in _PATH_CLS:
            i += 1
    for run_start, run_end in reversed(runs):
        dot = run_end - 1
        while dot > run_start:
            if text[dot] == ".":
                ext_len = _ext_at(text, dot)
                if ext_len and dot + 1 + ext_len <= n:
                    return dot + 1 + ext_len, i
            dot -= 1
    return -1, i


def extract_path_mentions(text: str) -> list[str]:
    """File paths the question names, first-mention order, case-deduplicated.

    Moved from ``AgentLoop`` (piece 3 of the loop decomposition) and rewritten
    from a quadratic regex into this single-pass scanner — see the block
    comment above for the measurements and the oracle tests for equivalence.
    """
    n = len(text)
    seen: set[str] = set()
    paths: list[str] = []
    pos = 0
    while pos < n:
        ch = text[pos]
        is_candidate = ch in _PATH_CLS or ch == "/"
        guarded = pos > 0 and text[pos - 1] in _PATH_GUARD
        if not is_candidate or guarded:
            pos += 1
            continue
        match_start = pos
        body_start = -1
        if (drive := _drive_body_start(text, pos)) >= 0:
            body_start = drive
        elif ch == "/":
            if pos + 1 < n and text[pos + 1] in _PATH_CLS:
                body_start = pos + 1        # bare leading slash
        else:
            body_start = pos
        if body_start < 0:
            pos += 1
            continue
        end, scan_stop = _scan_body(text, body_start)
        if end < 0:
            # No run the walk REACHED holds a dotted extension with a
            # non-empty head, so no later start inside the walked region can
            # succeed either (it would see a subset of the same dots). Skip
            # to the terminator — and only to it: the text beyond was never
            # examined. This bounded skip is what makes the scan linear.
            #
            # One candidate inside the walked region is NOT subsumed: a
            # drive prefix. The walk stops at ":", but its preceding letter
            # can start `X:/...` — a shape the region scan cannot see
            # because ":" is outside the body alphabet.
            if (
                0 < scan_stop < n and text[scan_stop] == ":"
                and not (scan_stop >= 2 and text[scan_stop - 2] in _PATH_GUARD)
                and (d_body := _drive_body_start(text, scan_stop - 1)) >= 0
            ):
                d_end, _ = _scan_body(text, d_body)
                if d_end >= 0:
                    path = text[scan_stop - 1:d_end].rstrip(".,;:!?)\"]}'")
                    key = path.casefold()
                    if key not in seen:
                        seen.add(key)
                        paths.append(path)
                    pos = d_end
                    continue
            pos = max(scan_stop, pos + 1)
            continue
        path = text[match_start:end].rstrip(".,;:!?)\"]}'")
        key = path.casefold()
        if key not in seen:
            seen.add(key)
            paths.append(path)
        pos = end
    return paths



def normalize_path_mention(path: str) -> str:
    out = path.strip().strip("\"'")
    out = out.replace("/", "\\")
    while out.startswith(".\\"):
        out = out[2:]
    return out.casefold()


def force_file_hint_read_when_explicit(
    planner_out: PlannerOutput,
    *,
    question: str,
    file_hint: str | None,
) -> PlannerOutput:
    if not file_hint:
        return planner_out
    if any(src.get("tool") == "file_read" for src in planner_out.sources):
        return planner_out
    if not explicitly_requests_hinted_file(question):
        return planner_out
    sources = list(planner_out.sources)
    sources.append(file_hint_source(file_hint))
    warnings = list(planner_out.warnings)
    warnings.append(
        "explicit file-read request used --file hint because planner selected no file_read"
    )
    return PlannerOutput(
        reasoning=(
            f"{planner_out.reasoning} "
            "Kernel added read-only file_read for explicit hinted-file request."
        ).strip(),
        sources=sources,
        raw_response=planner_out.raw_response,
        warnings=warnings,
    )


def prepare_multi_file_review(
    question: str,
    *,
    file_hint: str | None,
    workspace_root: Path | None,
    log: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Decide none / refusal / forced-plan for a multi-file review request.

    Moved from ``AgentLoop`` (piece 6): every predicate it orchestrates already
    lives in this module. The two loop facts it needs arrive as arguments —
    ``workspace_root`` (where file_read may look) and ``log`` (the trace
    callable) — so the module still knows nothing about the loop itself.
    """
    requested_paths = extract_path_mentions(question)
    if len(requested_paths) < 2 or not is_file_review_request(question):
        return {"kind": "none"}

    explicit_mode = is_explicit_multi_file_mode(question)
    # A request to CHANGE something is not a request to read files. The
    # review predicates scan the whole question for a single verb, so one
    # step inside a work order ("сравни результаты с baseline") turned a
    # refactor into "compare these files" — and the forced plan below then
    # removes every other tool from the cycle. Observed live: a task naming
    # the module to create and the module never to create had both treated
    # as documents to read, and was answered by reading one file — no
    # tests, no branch, no commit. The operator's explicit switch still
    # wins: it names the mode in words, which beats inferring it from verbs.
    if not explicit_mode and is_change_request(question):
        return {"kind": "none"}
    if file_hint and not explicit_mode:
        hint_norm = normalize_path_mention(file_hint)
        extra_paths = [
            path
            for path in requested_paths
            if normalize_path_mention(path) != hint_norm
        ]
        if extra_paths:
            available = file_hint
            extras = ", ".join(extra_paths)
            return {
                "kind": "refusal",
                "message": (
                    "Regular --file mode only permits the hinted file. "
                    f"Available evidence: {available}. "
                    f"Requested additional files were not reviewed: {extras}. "
                    "Use :ingest-source for additional files or explicit "
                    "multi-file review mode."
                ),
                "requested_paths": requested_paths,
                "file_hint": file_hint,
                "extra_paths": extra_paths,
            }
        return {"kind": "none"}

    if not explicit_mode and file_hint:
        return {"kind": "none"}
    if not explicit_mode and not looks_like_multi_file_review_without_hint(question):
        return {"kind": "none"}

    root = workspace_root
    if root is None:
        return {
            "kind": "refusal",
            "message": (
                "Multi-file review is unavailable because file_read is not "
                "registered in this agent session."
            ),
            "requested_paths": requested_paths,
        }

    valid_sources: list[dict[str, Any]] = []
    warnings: list[str] = []
    seen_targets: set[str] = set()
    rejected: list[dict[str, str]] = []
    for raw_path in requested_paths:
        item = validate_user_file_path(raw_path, workspace=root)
        if item["ok"] is not True:
            rejected.append({
                "path": raw_path,
                "reason": str(item["reason"]),
            })
            warnings.append(f"{raw_path}: {item['reason']}")
            continue
        target_key = str(item["target"]).casefold()
        if target_key in seen_targets:
            warnings.append(f"{raw_path}: duplicate path skipped")
            continue
        seen_targets.add(target_key)
        rel_path = item["relative_path"].as_posix()
        valid_sources.append({
            "tool": "file_read",
            "arguments": {"path": rel_path},
            "label": f"file:{rel_path}",
            "expected_outcome": "Non-empty UTF-8 text from the explicitly mentioned file.",
        })

    if not valid_sources:
        details = "; ".join(
            f"{item['path']}: {item['reason']}" for item in rejected
        ) or "no valid files were mentioned"
        return {
            "kind": "refusal",
            "message": (
                "Multi-file review could not start because no valid "
                f"workspace files passed preflight. {details}."
            ),
            "requested_paths": requested_paths,
            "rejected": rejected,
        }

    log(
        "multi_file_review_preflight",
        {
            "requested_paths": requested_paths,
            "accepted_paths": [src["arguments"]["path"] for src in valid_sources],
            "rejected": rejected,
            "warnings": warnings,
        },
    )
    return {
        "kind": "forced",
        "sources": valid_sources,
        "warnings": warnings,
        "rejected": rejected,
        "reasoning": (
            "Kernel explicit multi-file review: read only the user-mentioned "
            "workspace files that passed strict path preflight. "
            + ("Rejected/skipped: " + "; ".join(warnings) if warnings else "")
        ),
    }
