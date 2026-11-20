"""Deterministic classifiers for file requests: read vs change vs multi-file intent, and safe user-path validation (no LLM, no state)."""
from __future__ import annotations

import re
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tools.file_read import MAX_BYTES as FILE_READ_MAX_BYTES

if TYPE_CHECKING:  # pragma: no cover — annotation only
    # Runtime import of core.planner would pull in core.llm and break this module's no-LLM boundary.
    from collections.abc import Callable

    from core.planner import PlannerOutput


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


# Verbs stating intent to produce, run or record. Word-boundary matching: a substring scan would fire "implement" inside
# "implementation"; Russian forms spell their endings for the same reason.
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

# Punctuation a file name picks up: sentence/list marks and quoting (backticks included). Not named "token": secret scanners flag that.
_FILENAME_EDGE_PUNCT = ".,;:!?()[]{}<>«»\"'`"


def strip_file_tokens(text: str) -> str:
    """Drop whitespace-separated tokens that name a file (any ASCII-alphanumeric extension, so commit.log does not vote for "commit")."""
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
    """True when the request asks for work, not a reading; unambiguous verbs only, with path mentions removed first (commit.md is not an instruction to commit)."""
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
    # Canonicalize the workspace too (symlinks, Windows short names); the fence uses resolve().relative_to before any file probe (order pinned by a test).
    workspace = Path(workspace).resolve()
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


# extract_path_mentions: a linear scanner replacing a quadratic regex (its lookbehind guard still left O(n) starts after letters).
# Equivalence with the old regex is asserted by tests/test_file_request_intent_paths.py, which holds the original pattern as the oracle.

_PATH_CLS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789_.-"
)
_PATH_SLASH = frozenset("/\\")
#: A match may not start right after one of these; backslash is deliberately absent (.\main.py is a Windows path).
_PATH_GUARD = frozenset("/.-")
#: Alternation order preserved from the regex: at the same dot, py wins over pdf.
_PATH_EXTS = ("py", "md", "txt", "json", "yml", "yaml", "pdf")


def _ext_at(text: str, dot: int, exts: tuple[str, ...]) -> int:
    """Length of the extension right after ``text[dot]``, or 0."""
    for ext in exts:
        if text[dot + 1: dot + 1 + len(ext)].lower() == ext:
            return len(ext)
    return 0


def _drive_body_start(text: str, letter: int) -> int:
    """Body start after a drive prefix at text[letter], or -1; one helper for both the candidate branch and the failed-scan recheck."""
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


def _scan_body(
    text: str, start: int, exts: tuple[str, ...] = _PATH_EXTS
) -> tuple[int, int]:
    """(match_end, scan_stop) for the path body at start; match_end is -1 when no match.

    On failure the caller may skip only to scan_stop (beyond it nothing was examined). Picks the rightmost dot with a known
    extension and a non-empty head, as the old regex's greedy star plus backtracking did.
    """
    n = len(text)
    runs: list[tuple[int, int]] = []
    i = start
    while i < n and text[i] in _PATH_CLS:
        run_start = i
        while i < n and text[i] in _PATH_CLS:
            i += 1
        runs.append((run_start, i))
        # A single slash joins the next run; a double slash or a trailing slash ends the body.
        if i < n and text[i] in _PATH_SLASH and i + 1 < n and text[i + 1] in _PATH_CLS:
            i += 1
    for run_start, run_end in reversed(runs):
        dot = run_end - 1
        while dot > run_start:
            if text[dot] == ".":
                ext_len = _ext_at(text, dot, exts)
                if ext_len and dot + 1 + ext_len <= n:
                    return dot + 1 + ext_len, i
            dot -= 1
    return -1, i


def extract_path_mentions(
    text: str, *, exts: tuple[str, ...] = _PATH_EXTS
) -> list[str]:
    """File paths the question names, first-mention order, case-deduplicated."""
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
                body_start = pos + 1
        else:
            body_start = pos
        if body_start < 0:
            pos += 1
            continue
        end, scan_stop = _scan_body(text, body_start, exts)
        if end < 0:
            # No walked run holds a dotted extension, so no later start inside the walked region can succeed; skip to the
            # terminator only (keeps the scan linear). Not subsumed: a drive prefix, whose letter precedes the ':' that stops the walk.
            if (
                0 < scan_stop < n and text[scan_stop] == ":"
                and not (scan_stop >= 2 and text[scan_stop - 2] in _PATH_GUARD)
                and (d_body := _drive_body_start(text, scan_stop - 1)) >= 0
            ):
                d_end, _ = _scan_body(text, d_body, exts)
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



#: Extensions the completion machinery scans for; wider than the loop's own list and shared by two modules to avoid vocabulary drift.
OBLIGATION_PATH_EXTS: tuple[str, ...] = (
    "py", "md", "txt", "json", "yml", "yaml", "pdf",
    "jsonl", "toml", "ini", "cfg", "log",
)


def paths_mentioned(text: str) -> tuple[str, ...]:
    """Workspace paths named in the text, de-duplicated, order preserved."""
    return tuple(extract_path_mentions(text or "", exts=OBLIGATION_PATH_EXTS))


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
    # dataclasses.replace, not PlannerOutput(...): the constructor needs the forbidden runtime import, and replace keeps other fields.
    return replace(
        planner_out,
        reasoning=(
            f"{planner_out.reasoning} "
            "Kernel added read-only file_read for explicit hinted-file request."
        ).strip(),
        sources=sources,
        warnings=warnings,
    )


def prepare_multi_file_review(
    question: str,
    *,
    file_hint: str | None,
    workspace_root: Path | None,
    log: Callable[[str, dict[str, Any]], None],
) -> dict[str, Any]:
    """Decide none / refusal / forced-plan for a multi-file review request."""
    requested_paths = extract_path_mentions(question)
    if len(requested_paths) < 2 or not is_file_review_request(question):
        return {"kind": "none"}

    explicit_mode = is_explicit_multi_file_mode(question)
    # A change request is not a reading request: review predicates scan the whole question for one verb, so a work order
    # would be turned into "compare these files". The operator's explicit mode switch still wins.
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
        # A guessed mode may not refuse the whole question: no named file existing argues against the guess; refusal stays with a mode named in words.
        if not explicit_mode:
            log("multi_file_review_guess_dropped", {
                "requested_paths": requested_paths, "rejected": rejected,
            })
            return {"kind": "none"}
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
