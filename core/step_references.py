"""Carries an observed value from step A into the arguments of step B via `{{step:<id or order>.output}}`: only the output of an already executed step, no computation or slicing; resolved before the recipient runs; an unresolved reference stays a named error; a whole-argument reference keeps the value's TYPE; a reference form in PROSE is not a reference when plan steps are known (a dangling number still is an error)."""
from __future__ import annotations

import re
from collections.abc import Collection
from pathlib import Path
from typing import Any

#: {{step:<ref>.output}} is the only permitted form.
_REFERENCE_RE = re.compile(
    r"\{\{\s*step:(?P<ref>[A-Za-z0-9_\-]+)\.output\s*\}\}"
)

#: {{step:<ref>.output.<field>}} / {{step:<ref>.output[0]}}: a path into the output; recognised and refused as an error with a named rule, not passed as a literal string to the tool.
_FIELD_PATH_RE = re.compile(
    r"\{\{\s*step:(?P<ref>[A-Za-z0-9_\-]+)\.output(?P<path>[.\[][^{}]*?)\s*\}\}"
)


class UnresolvedStepReference(ValueError):
    """Reference to a step with no result; a separate type because it is NOT a tool error: the recipient must not run at all and the reason must reach the journal whole."""


def has_step_reference(value: Any) -> bool:
    """Whether the arguments hold any reference to another step; a reference with a path (`.output.<field>`) counts, so it fails in the resolver with a named rule."""
    if isinstance(value, str):
        return bool(_REFERENCE_RE.search(value) or _FIELD_PATH_RE.search(value))
    if isinstance(value, dict):
        return any(has_step_reference(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(has_step_reference(v) for v in value)
    return False


def referenced_steps(value: Any) -> tuple[str, ...]:
    """All steps referenced by the arguments, in order of appearance."""
    found: list[str] = []

    def _walk(node: Any) -> None:
        if isinstance(node, str):
            for match in _REFERENCE_RE.finditer(node):
                ref = match.group("ref")
                if ref not in found:
                    found.append(ref)
        elif isinstance(node, dict):
            for item in node.values():
                _walk(item)
        elif isinstance(node, (list, tuple)):
            for item in node:
                _walk(item)

    _walk(value)
    return tuple(found)


def _is_reference(
    ref: str, outputs: dict[str, Any], plan_steps: Collection[str] | None,
) -> bool:
    """Reference or prose mentioning the reference form (see module docstring); without `plan_steps` every form is a reference (strict old behaviour)."""
    if plan_steps is None or ref in outputs or ref in plan_steps:
        return True
    return ref.isdigit() or ref.startswith("step_")


def _reject_field_path(text: str) -> None:
    """A `{{step:N.output.<field>}}` reference is an error with a named rule."""
    match = _FIELD_PATH_RE.search(text)
    if match is None:
        return
    ref, path = match.group("ref"), match.group("path").strip()
    raise UnresolvedStepReference(
        f"step reference {match.group(0).strip()} is not a supported form: "
        f"a path into the output ({path!r}) cannot be resolved; the only "
        f"allowed form is {{{{step:{ref}.output}}}} (the whole output)"
    )


#: Output of these tools is never file content (search, listing, web page); putting it into a write's content writes foreign text instead of your own.
_NEVER_FILE_CONTENT = frozenset({
    "list_dir", "find_in_files", "web_fetch", "web_search",
    "semantic_scholar_search", "rss_fetch", "read_logs",
})
#: A file_read window with start_line/end_line is a view with line numbers, not file content.
_NUMBERED_WINDOW_RE = re.compile(r"\[[^\]\n]+ lines \d+-\d+ of \d+\]\n")
#: Code of another kind is not a copy (a .py module read into patch.md); text and data copy legitimately ("copy src.txt to report.json").
_CODE_SUFFIXES = frozenset({
    ".py", ".js", ".ts", ".tsx", ".jsx", ".sh", ".ps1", ".bat", ".cmd",
    ".c", ".h", ".cpp", ".rs", ".go", ".java", ".rb", ".php", ".sql",
})


def _code_kind_changes(source_suffix: str, target_suffix: str) -> bool:
    """Code moves into a file of another kind (or code into non-code): not a copy."""
    return source_suffix != target_suffix and (
        source_suffix in _CODE_SUFFIXES or target_suffix in _CODE_SUFFIXES)


def reject_content_reference(
    tool: str | None,
    arguments: dict[str, Any],
    sources: dict[str, tuple[str, str]],
    outputs: dict[str, Any],
) -> None:
    """Reading in place of own text in a write's `content` is an error. Legitimate: command output (python_probe, shell_exec) into a file and a copy of a WHOLE file into a file of the same kind. Rejected: search/list/web-page output, a line-numbered window, a whole file of another kind. `sources` maps reference -> (tool, path), `outputs` is its output."""
    if tool != "file_write":
        return
    content = arguments.get("content")
    if not isinstance(content, str):
        return
    target_suffix = Path(str(arguments.get("path") or "")).suffix.lower()
    for ref in referenced_steps(content):
        source_tool, source_path = sources.get(ref, ("", ""))
        if source_tool in _NEVER_FILE_CONTENT:
            why = f"the output of {source_tool} is never a file's content"
        elif source_tool != "file_read":
            continue
        elif _NUMBERED_WINDOW_RE.match(str(outputs.get(ref) or "")):
            why = "a file_read line window is numbered lines for reading, not the file"
        elif _code_kind_changes(Path(source_path).suffix.lower(), target_suffix):
            why = (f"{source_path} copied into a {target_suffix or 'suffix-less'} file "
                   "is not a copy")
        else:
            continue
        raise UnresolvedStepReference(
            f"file_write content takes step {ref}'s output ({source_tool} {source_path}): "
            f"{why}. Write your own text into content; a reference carries only a "
            f"command's output or a whole-file copy"
        )


def _delivered(ref: str, output: Any) -> Any:
    """What a reference carries: the step result, for a command its stdout; a failed, interrupted or truncated command is no result, so the reference is not resolved and names the reason."""
    if not (isinstance(output, dict) and "stdout" in output and "exit_code" in output):
        return output
    reason = (
        f"named workspace files it was not given {output['missing_inputs']} "
        "(list them in inputs)" if output.get("missing_inputs")
        else "timed out" if output.get("timed_out")
        else f"exited with {output.get('exit_code')}" if output.get("exit_code") != 0
        else "its stdout was truncated" if output.get("stdout_truncated")
        else ""
    )
    if reason:
        tail = str(output.get("stderr") or "").strip()[-200:]
        raise UnresolvedStepReference(
            f"step reference {{{{step:{ref}.output}}}}: the command {reason}, so its "
            f"output is not a result" + (f" (stderr: {tail})" if tail else "")
        )
    return output.get("stdout")


#: Arguments where pasted output becomes an address; an address is one name (a whole step output pasted into a path made file_read look for a multi-name "file").
_ADDRESS_KEYS = frozenset({"path", "url"})


def _one_line(ref: str, value: Any) -> str:
    text = value.strip() if isinstance(value, str) else None
    if text is None or "\n" in text or "\r" in text:
        kind = "multi-line" if text is not None else type(value).__name__
        raise UnresolvedStepReference(
            f"step reference {{{{step:{ref}.output}}}} is pasted inside an address "
            f"(path/url), but its result is {kind}; an address takes one name — "
            f"pick the name first, then reference that step"
        )
    return text


def _resolve_string(
    text: str, outputs: dict[str, Any], plan_steps: Collection[str] | None,
    address: bool = False, whole_is_one_name: bool = False,
) -> Any:
    _reject_field_path(text)
    match = _REFERENCE_RE.fullmatch(text.strip())
    if match is not None and _is_reference(match.group("ref"), outputs, plan_steps):
        # A reference that fills the whole argument returns the value AS IS, so a list stays a list and a number a number.
        ref = match.group("ref")
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        value = _delivered(ref, outputs[ref])
        # A whole-argument reference keeps the value type, which the tool judges; except url, where a search result list is not an address and the refusal must say what to do first. The rule is no wider than its ground: path behaves as before.
        return _one_line(ref, value) if whole_is_one_name else value

    def _substitute(m: re.Match[str]) -> str:
        ref = m.group("ref")
        if not _is_reference(ref, outputs, plan_steps):
            return m.group(0)
        if ref not in outputs:
            raise UnresolvedStepReference(
                f"step reference {{{{step:{ref}.output}}}} has no result: "
                f"known steps are {sorted(outputs)}"
            )
        value = _delivered(ref, outputs[ref])
        return _one_line(ref, value) if address else str(value)

    return _REFERENCE_RE.sub(_substitute, text)


def resolve_step_references(
    value: Any,
    outputs: dict[str, Any],
    *,
    plan_steps: Collection[str] | None = None,
) -> Any:
    """Substitute step results into arguments; `outputs` maps step id or number -> its output, delivered exactly as measured. `plan_steps` lists the ids and numbers of ALL plan steps; with them a form that names no step or number reads as prose and is left alone."""
    if isinstance(value, str):
        return _resolve_string(value, outputs, plan_steps)
    if isinstance(value, dict):
        return {
            k: (_resolve_string(v, outputs, plan_steps, address=True, whole_is_one_name=(k == "url"))
                if k in _ADDRESS_KEYS and isinstance(v, str)
                else resolve_step_references(v, outputs, plan_steps=plan_steps))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [resolve_step_references(v, outputs, plan_steps=plan_steps) for v in value]
    if isinstance(value, tuple):
        return tuple(
            resolve_step_references(v, outputs, plan_steps=plan_steps) for v in value
        )
    return value


def renumber_step_references(
    before: list[dict[str, Any]], after: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Rewrite `{{step:N.output}}` to the new step order: the reference number is the step's place in the PLANNER's plan, so when steps are reordered it must follow the step. Steps match by object identity; a reference to a step that is gone is left alone and fails aloud on resolution."""
    old_to_new: dict[str, str] = {}
    positions = {id(src): i for i, src in enumerate(after, start=1)}
    for old, src in enumerate(before, start=1):
        new = positions.get(id(src))
        if new is not None and new != old:
            old_to_new[str(old)] = str(new)
    if not old_to_new:
        return after

    def fix(value: Any) -> Any:
        if isinstance(value, str):
            return _REFERENCE_RE.sub(
                lambda m: m.group(0).replace(m.group("ref"), old_to_new[m.group("ref")], 1)
                if m.group("ref") in old_to_new else m.group(0), value)
        if isinstance(value, dict):
            return {k: fix(v) for k, v in value.items()}
        if isinstance(value, list):
            return [fix(v) for v in value]
        return value

    for src in after:
        if isinstance(src.get("arguments"), dict):
            src["arguments"] = fix(src["arguments"])
    return after
