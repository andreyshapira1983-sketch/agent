"""Parsing of the planner LLM's raw output (§3 Cognitive Core: Planning).

Fence stripping, DLP-redacted previews for diagnostics, and the tolerant
JSON extraction that turns raw model text into a plan dict plus a parse
diagnostics record. Pure text processing: no LLM call, no planner state.
Moved verbatim (de-static + dedent only) from core/planner.py.
"""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any


def _strip_markdown_fence(text: str) -> str | None:
    """Return the body of a ```` ``` ````/```` ```json ```` block, else ``None``.

    This replaces ``^```(?:json)?\\s*(.*?)\\s*```\\s*$`` under ``DOTALL``. In
    that pattern ``.`` and ``\\s`` both matched a blank, so every space between
    the opening fence and the body could be claimed by either side and the
    engine tried each split before concluding there was no closing fence.
    Measured: 2.9 s for 2000 trailing blanks, 65.6 s for 4000 — and the input
    is a model reply, so a truncated answer that opens a fence and never closes
    it is enough to stall the planner. Reading the string directly is linear.

    The rules are the old ones, kept literally: the fence must open at the very
    first character; ``json`` is stripped only in lower case, as ``(?:json)?``
    had no ``IGNORECASE``; trailing whitespace after the closing fence is
    allowed; the closing fence is the last one, since ``\\s*$`` forced the lazy
    body to grow past any earlier ```` ``` ````; and the body is stripped.
    Checked against the old pattern on 26 hand-written cases and 2793 generated
    fence-like strings: identical answers, every one.
    """
    if not text.startswith("```"):
        return None
    body = text[3:]
    if body[:4] == "json":
        body = body[4:]
    tail = body.rstrip()
    if not tail.endswith("```"):
        return None
    return tail[:-3].strip()



# Max characters of raw planner output echoed into diagnostics. Keeps trace
# logs bounded and, combined with DLP redaction, avoids leaking full secrets.
_RAW_PREVIEW_LIMIT = 200

def _sanitized_preview(raw: str, limit: int = _RAW_PREVIEW_LIMIT) -> str:
    """Return a DLP-redacted, single-line, length-capped preview of *raw*.

    Credentials/PII are redacted before truncation so a leaked secret can
    never appear even partially, and newlines are escaped so the preview
    stays on one trace line. Purely local (regex) — no LLM/provider call.
    """
    text = (raw or "").strip()
    if not text:
        return ""
    try:
        from core.redaction import redact_dlp_text  # local import: avoid cycles
        safe, _secret_findings, _pii_findings = redact_dlp_text(text)
    except Exception:  # noqa: BLE001 — preview must never crash planning
        safe = text
    safe = safe.replace("\r", "").replace("\n", "\\n")
    if len(safe) > limit:
        hidden = len(safe) - limit
        safe = f"{safe[:limit]}… [+{hidden} chars truncated]"
    return safe

def parse_json(
    raw: str,
) -> tuple[dict[str, Any] | None, list[str], dict[str, Any]]:
    warnings: list[str] = []
    text = raw.strip()

    # Structured diagnostics (TD-003). Always populated so callers can show
    # *why* parsing succeeded or failed without re-deriving it. `stage` names
    # where parsing ended, `fallback` names which recovery path was used.
    diagnostics: dict[str, Any] = {
        "stage": "start",
        "reason": "",
        "json_block_found": False,
        "fallback": "none",
        "raw_preview": _sanitized_preview(raw),
        "raw_length": len(raw),
    }

    # Empty output is NOT a parse failure. Reporting `json_decode_error`
    # for zero characters sends every reader — human or agent — looking for
    # malformed JSON that was never emitted. The real event is that the
    # model returned nothing at all (observed with OpenAI reasoning models
    # whose whole `max_completion_tokens` budget is consumed by internal
    # reasoning, leaving no visible content while the API still reports
    # success). Name it precisely so the remedy — raise the budget, not fix
    # the JSON — is obvious from the log.
    if not text:
        warnings.append("empty_model_output")
        diagnostics["stage"] = "empty_output"
        diagnostics["fallback"] = "empty_plan"
        diagnostics["reason"] = (
            "model returned no text at all (0 chars); nothing to parse. "
            "Typical cause: the token budget was exhausted before any "
            "visible output was produced."
        )
        return None, warnings, diagnostics

    # Strip a leading ```json or ``` fence if present.
    fenced = _strip_markdown_fence(text)
    if fenced is not None:
        text = fenced
        warnings.append("stripped_markdown_fence")
        diagnostics["json_block_found"] = True
        diagnostics["fallback"] = "markdown_fence"

    # Direct parse first.
    diagnostics["stage"] = "direct_parse"
    try:
        obj = json.loads(text)
        if isinstance(obj, dict):
            diagnostics["stage"] = "parsed"
            diagnostics["reason"] = "ok"
            return obj, warnings, diagnostics
        warnings.append("top_level_not_object")
        diagnostics["reason"] = (
            f"top-level JSON was {type(obj).__name__}, expected an object"
        )
    except json.JSONDecodeError as exc:
        diagnostics["reason"] = (
            f"JSON decode error at line {exc.lineno} col {exc.colno}: {exc.msg}"
        )

    # Fallback: find first '{' and matching last '}'.
    start = text.find("{")
    end = text.rfind("}")
    if start != -1 and end != -1 and end > start:
        diagnostics["json_block_found"] = True
        diagnostics["stage"] = "substring_extract"
        try:
            obj = json.loads(text[start : end + 1])
            if isinstance(obj, dict):
                warnings.append("extracted_json_substring")
                diagnostics["stage"] = "parsed"
                diagnostics["fallback"] = "substring"
                diagnostics["reason"] = "recovered JSON object from surrounding text"
                return obj, warnings, diagnostics
            warnings.append("top_level_not_object")
            diagnostics["reason"] = (
                f"extracted substring was {type(obj).__name__}, expected an object"
            )
        except json.JSONDecodeError as exc:
            diagnostics["reason"] = (
                f"substring JSON decode error at line {exc.lineno} "
                f"col {exc.colno}: {exc.msg}"
            )

    warnings.append("json_decode_error")
    diagnostics["stage"] = "failed"
    diagnostics["fallback"] = "empty_plan"
    if not diagnostics["reason"]:
        diagnostics["reason"] = "no JSON object found in planner output"
    return None, warnings, diagnostics


_ANYWHERE_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def embedded_json_objects(text: str) -> Iterator[str]:
    """Every balanced ``{...}`` span in `text`, left to right.

    Yields rather than returning the first, because the first is often not the
    answer: a model narrating "the block builds {'k': 1} before writing" leaves
    a balanced span that parses as nothing, and an earlier illustrative object
    ("here is the shape I will return: {...}") parses fine while being the wrong
    object. Taking only the leftmost defeats the fix in exactly the
    narrating-model case it exists for.

    Brace counting is string-aware: a `{` inside a JSON string value — common
    here, since `proposed_content` carries Python code — must not open a level,
    and the matching `}` must not close one early.
    """
    index = 0
    length = len(text)
    while index < length:
        start = text.find("{", index)
        if start < 0:
            return
        depth = 0
        in_string = False
        escaped = False
        closed_at = -1
        for pos in range(start, length):
            char = text[pos]
            if in_string:
                if escaped:
                    escaped = False
                elif char == "\\":
                    escaped = True
                elif char == '"':
                    in_string = False
                continue
            if char == '"':
                in_string = True
            elif char == "{":
                depth += 1
            elif char == "}":
                depth -= 1
                if depth == 0:
                    closed_at = pos
                    break
        if closed_at < 0:
            # Unbalanced from here on: nothing further can close either.
            return
        yield text[start:closed_at + 1]
        index = closed_at + 1


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort: the JSON object inside a raw LLM reply, or ``None``.

    The one shared answer to "the model was asked for JSON and wrapped it in
    prose/fences anyway". Tolerances, strongest last, first hit wins:

      1. a reply that IS the object (after stripping a leading fence);
      2. an object inside a fence anywhere in the text;
      3. the first-``{`` .. last-``}`` substring;
      4. every balanced ``{...}`` span, left to right (string-aware).

    Replaces three weaker per-module copies (subagent_memory_scope,
    self_build_producer; repair_proposal keeps its domain envelope but shares
    ``embedded_json_objects``). No diagnostics: callers that need to explain a
    failure (the planner) use ``parse_json`` instead.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    t = text.strip()
    fenced = _strip_markdown_fence(t)
    if fenced is not None:
        t = fenced
    candidates: list[str] = [t]
    fence_match = _ANYWHERE_FENCE_RE.search(t)
    if fence_match:
        candidates.append(fence_match.group(1))
    start, end = t.find("{"), t.rfind("}")
    if start != -1 and end > start:
        candidates.append(t[start : end + 1])
    for candidate in (*candidates, *embedded_json_objects(t)):
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
