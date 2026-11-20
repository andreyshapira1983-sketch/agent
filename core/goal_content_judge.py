"""Goal judge that reads the product itself and checks each requirement by a quoted passage found in the file."""
from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

#: How much product text the judge sees: per file and in total.
_PER_FILE_CHARS = 12_000
_TOTAL_CHARS = 24_000
_MAX_QUESTIONS = 5
#: A shorter quote is not proof (it can match by chance).
_MIN_QUOTE = 12

_SYSTEM = (
    "You check whether a finished piece of work meets its success criterion. You get the CRITERION "
    "and the FILE CONTENT the work produced. Split the criterion into 1-5 concrete yes/no requirement "
    "questions (one per requirement it states) about WHAT THE FILE SAYS - that the file exists is "
    "already checked, do not ask it. Answer each STRICTLY from the file content. For every "
    "'yes' give a verbatim quote (12-200 characters) copied exactly from the file that shows it; if you "
    "cannot quote it, the answer is 'no'. Reply with JSON only: "
    '[{"question": "...", "answer": "yes" | "no", "quote": "..."}]'
)


def _norm(text: str) -> str:
    return re.sub(r"\s+", " ", text or "").strip().lower()


def _read(workspace: Any, paths: list[str]) -> list[tuple[str, str]]:
    """(path, judge-visible slice) for each product file."""
    parts, total = [], 0
    for rel in paths:
        try:
            text = (Path(workspace or ".") / rel).read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        room = min(_PER_FILE_CHARS, _TOTAL_CHARS - total)
        # logs are appended: the newest product is at the end
        chunk = text[-room:] if rel.endswith((".jsonl", ".log")) and room > 0 else text[:room]
        if not chunk:
            break
        parts.append((rel, chunk))
        total += len(chunk)
    return parts


def _parse(raw: str) -> list[dict[str, str]] | None:
    m = re.search(r"\[.*\]", raw or "", re.DOTALL)
    if not m:
        return None
    try:
        rows = json.loads(m.group(0))
    except ValueError:
        return None
    if not isinstance(rows, list):
        return None
    out = [{"question": str(r.get("question") or "")[:200], "answer": str(r.get("answer") or "").lower(),
            "quote": str(r.get("quote") or "")} for r in rows if isinstance(r, dict)]
    return out[:_MAX_QUESTIONS] or None


def judge_content(success_check: str, fresh_paths: list[str], workspace: Any, llm: Any) -> dict[str, Any] | None:
    """Check the product content against the criterion; None when it cannot be judged.

    Returns {"met": bool, "unmet": [questions], "checked": n}; None (no model, no text, unparseable answer) keeps the file-based verdict.
    """
    if llm is None or not fresh_paths:
        return None
    parts = _read(workspace, fresh_paths)
    if not parts:
        return None
    content = "\n\n".join(f"=== FILE {rel} ===\n{chunk}" for rel, chunk in parts)
    try:
        raw = llm.complete(system=_SYSTEM, user=f"CRITERION:\n{success_check}\n\nFILE CONTENT:\n{content}",
                           max_tokens=900)
    except Exception:  # noqa: BLE001 — the content judge must not break the verdict
        return None
    rows = _parse(str(getattr(raw, "text", raw)))
    if rows is None:
        return None
    # quotes are searched in file text, not in the "=== FILE ... ===" headers
    haystack = _norm("\n".join(chunk for _, chunk in parts))
    unmet = []
    for row in rows:
        quote = _norm(row["quote"])
        backed = row["answer"] == "yes" and len(quote) >= _MIN_QUOTE and quote in haystack
        if not backed:
            unmet.append(row["question"] or "(unnamed requirement)")
    return {"met": not unmet, "unmet": unmet, "checked": len(rows)}
