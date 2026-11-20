"""Parses the planner LLM's raw output: fence stripping, DLP-redacted previews and tolerant JSON extraction into a plan dict plus a parse-diagnostics record. Pure text processing."""
from __future__ import annotations

import json
import re
from collections.abc import Iterator
from typing import Any


def _strip_markdown_fence(text: str) -> str | None:
    """Body of a fenced code block (plain or json), else ``None``."""
    if not text.startswith("```"):
        return None
    body = text[3:]
    if body[:4] == "json":
        body = body[4:]
    tail = body.rstrip()
    if not tail.endswith("```"):
        return None
    return tail[:-3].strip()



# Max characters of raw planner output echoed into diagnostics: bounds trace logs and, with DLP redaction, limits secret leakage.
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

    # Structured diagnostics, always populated so callers can show why parsing succeeded or failed;
    # `stage` names where parsing ended, `fallback` the recovery path used.
    diagnostics: dict[str, Any] = {
        "stage": "start",
        "reason": "",
        "json_block_found": False,
        "fallback": "none",
        "raw_preview": _sanitized_preview(raw),
        "raw_length": len(raw),
    }

    # Empty output is NOT a parse failure: reporting `json_decode_error` for zero characters sends readers hunting
    # for malformed JSON never emitted. The model returned nothing (seen with reasoning models whose whole token
    # budget goes to internal reasoning); name it so the remedy, raising the budget, is obvious from the log.
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

# After this many unbalanced-brace restarts the scanner gives up: each restart rescans the tail, so unbounded
# restarts on a wall of stray '{' would be quadratic. Real replies carry a handful.
_SCAN_RESTART_CAP = 32


def embedded_json_objects(text: str) -> Iterator[str]:
    """Every balanced ``{...}`` span in `text`, left to right.

    Brace counting is string-aware: a `{` inside a JSON string value —
    common here, since `proposed_content` carries Python code — must not
    open a level, and the matching `}` must not close one early.
    """
    index = 0
    restarts = 0
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
            # Unbalanced at THIS start, but an object opened later can still be balanced ('{unclosed {"a": 1}'), so restart
            # from the next '{'. Restarts are capped (see above) to bound the O(n^2) worst case.
            restarts += 1
            if restarts > _SCAN_RESTART_CAP:
                return
            index = start + 1
            continue
        yield text[start:closed_at + 1]
        index = closed_at + 1


#: Что JSON признаёт после обратного слэша (RFC 8259). `u` здесь нет: он
#: валиден ТОЛЬКО с четырьмя шестнадцатеричными цифрами следом, иначе это
#: обычный слэш — как в пути `C:\users\...` (замечание ревью #303).
_JSON_ESCAPABLE = frozenset('"\\/bfnrt')
_HEX = frozenset("0123456789abcdefABCDEF")


def escape_stray_backslashes(text: str) -> str:
    """Double a lone backslash that precedes a character JSON does not accept as an escape.

        Valid pairs (newline, quote, backslash, unicode escapes) are left alone: a pair is consumed whole,
        so its second character is not mistaken for a new backslash.
    """
    out: list[str] = []
    i = 0
    while i < len(text):
        ch = text[i]
        if ch != "\\":
            out.append(ch)
            i += 1
            continue
        nxt = text[i + 1] if i + 1 < len(text) else ""
        if nxt in _JSON_ESCAPABLE:
            out.append(ch + nxt)
            i += 2
        elif nxt == "u" and len(text) >= i + 6 and set(text[i + 2:i + 6]) <= _HEX:
            out.append(text[i:i + 6])
            i += 6
        else:
            out.append("\\\\")
            i += 1
    return "".join(out)


def extract_json_object(text: str) -> dict[str, Any] | None:
    """Best-effort: the JSON object inside a raw LLM reply, or ``None``.

        Tolerances, strongest last, first hit wins: (1) the reply IS the object (after stripping a leading fence);
        (2) an object inside a fence anywhere in the text; (3) the first-``{`` .. last-``}`` substring;
        (4) every balanced ``{...}`` span, left to right (string-aware).
        No diagnostics: callers that need to explain a failure (the planner) use ``parse_json`` instead.
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
    # dict.fromkeys: when the reply IS the object the candidates coincide, and without it the same text would be
    # parsed (and repaired) several times.
    scanned = list(dict.fromkeys([*candidates, *embedded_json_objects(t)]))
    for candidate in scanned:
        try:
            obj = json.loads(candidate)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    # Last resort: a lone backslash before a character JSON does not accept as an escape (models write this when
    # continuing a line of code). Healthy replies never reach here; only those that already failed to parse.
    for candidate in scanned:
        repaired = escape_stray_backslashes(candidate)
        if repaired == candidate:
            continue
        try:
            obj = json.loads(repaired)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None
