"""Admission rules for one planner step: the whitelist the model cannot argue with."""
from __future__ import annotations

import hashlib
import ipaddress
import re
from pathlib import Path
from typing import Any

from core.step_references import has_step_reference
from core.subagent_predictions import expect_sources_arg, subagent_reason

_PLACEHOLDER_HOSTS = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "www.example.com", "www.example.org", "www.example.net", "www.example.edu",
})

_PLACEHOLDER_TLDS = (".example", ".invalid", ".test", ".localhost")

#: Looks like a host name: dotted labels with an alphabetic ending (or bare `localhost`), then optional port and path; no spaces.
_HOSTLIKE_RE = re.compile(
    r"^(?:localhost|[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?"
    r"(?:\.[A-Za-z0-9](?:[A-Za-z0-9-]*[A-Za-z0-9])?)*\.[A-Za-z]{2,})"
    r"(?::\d{1,5})?(?:[/?#]\S*)?$"
)


def _url_host(url_lower: str) -> str:
    """Extract the bare host from an already http/https, ASCII, lowercased URL."""
    after_scheme = url_lower.split("://", 1)[-1]
    authority = after_scheme.split("/", 1)[0]
    authority = authority.rsplit("@", 1)[-1]  # userinfo ends at the LAST '@'
    if authority.startswith("["):             # bracketed IPv6 literal
        return authority.partition("]")[0].lstrip("[")
    return authority.split(":", 1)[0].strip(".")


def _is_local_network_host(host: str) -> bool:
    """True when the parsed host targets the local network."""
    if host == "localhost" or host.startswith("localhost."):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return not ip.is_global


def _is_placeholder_url(url_lower: str) -> bool:
    """True for reserved documentation/example/test hosts (never real targets)."""
    host = _url_host(url_lower)
    if not host:
        return False
    if host in _PLACEHOLDER_HOSTS:
        return True
    return any(host.endswith(tld) for tld in _PLACEHOLDER_TLDS)


#: file_read window default: a window is asked for when the planner knows WHERE to look, so it is short.
_DEFAULT_LINE_WINDOW = 60
_MAX_LINE_WINDOW = 400



# How much of argv[1] may appear in a label; the digest carries uniqueness, so an unbounded token must not reach every journal line.
_LABEL_ARG_CHARS = 32


def _shell_label(argv: list[str]) -> str:
    """Short, collision-resistant label for one shell_exec step.

    Lossless while it can be (at most two tokens and argv[1] within ``_LABEL_ARG_CHARS``); once anything visible is dropped,
    the whole argv is folded into a 24-bit digest.
    """
    cmd = argv[0].strip().lower() if argv else ""
    head = f"shell_exec:{cmd}"
    truncated = False
    if len(argv) > 1:
        arg = argv[1]
        truncated = len(arg) > _LABEL_ARG_CHARS
        head += f" {arg[:_LABEL_ARG_CHARS]}…" if truncated else f" {arg}"
    if len(argv) <= 2 and not truncated:
        # only here is the label still the whole command; a truncated argv[1] falls through to the digest
        return head
    digest = hashlib.blake2s(
        repr(argv).encode("utf-8", "replace"), digest_size=3
    ).hexdigest()
    return f"{head} #{digest}"


#: Cap on experiment code: a measurement, not a program (tools/python_probe.py).
_PROBE_CODE_CAP = 4000


def _sanitize_python_probe(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Admission for the lab step: its form only; depth (AST effect gate, container) is the tool's own."""
    code = args.get("code")
    if not isinstance(code, str) or not code.strip():
        warnings.append(f"step[{idx}]: python_probe without code, dropped")
        return None
    if len(code) > _PROBE_CODE_CAP:
        warnings.append(
            f"step[{idx}]: python_probe code too long "
            f"({len(code)} > {_PROBE_CODE_CAP}), dropped"
        )
        return None
    arguments: dict[str, Any] = {"code": code}
    timeout = args.get("timeout_seconds")
    if timeout is not None:
        if not isinstance(timeout, int) or not 1 <= timeout <= 60:
            warnings.append(
                f"step[{idx}]: python_probe timeout_seconds must be 1..60, dropped"
            )
            return None
        arguments["timeout_seconds"] = timeout
    inputs = args.get("inputs")  # workspace files to compute over
    # an empty value equals an absent key; a bare string is NOT widened to a one-item list: the contract is "list" in both layers (here and tools/python_probe.py) and a string would be split per character
    if isinstance(inputs, (list, str)) and not inputs:
        inputs = None
    if inputs is not None:
        if not (isinstance(inputs, list)
                and all(isinstance(p, str) and p.strip() for p in inputs)):
            warnings.append(
                f"step[{idx}]: python_probe inputs must be a list of paths, "
                f"got {type(inputs).__name__}, dropped"
            )
            return None
        arguments["inputs"] = inputs
    first = code.strip().splitlines()[0][:50]
    return {
        "tool": "python_probe",
        "arguments": arguments,
        "label": f"python_probe:{first}",
        "expected_outcome": (
            "Measured behaviour of THIS runtime: exit_code/stdout/stderr of a "
            "small experiment; a failing snippet is itself the answer."
        ),
    }


def _url_refusal(url: str) -> tuple[str | None, str]:
    """Why a web_fetch URL is not allowed (or None), and the URL after completion; the same rules at planning and after substitution."""
    if len(url) > 2048:
        return f"too long ({len(url)} > 2048)", url
    if not url.isascii():
        return "not ASCII", url
    # a bare host name is a URL with the default scheme; only host-like strings are completed, and all locks run AFTER completion
    if "://" not in url and _HOSTLIKE_RE.match(url.strip()):
        url = "https://" + url.strip()
    url_lower = url.lower()
    if not url_lower.startswith(("http://", "https://")):
        return f"must start with http:// or https://, got '{url[:80]}'", url
    # Block obvious SSRF shapes BEFORE the tool layer.
    if _is_local_network_host(_url_host(url_lower)):
        return "targets local network", url
    if _is_placeholder_url(url_lower):
        return "is a placeholder/example host", url
    return None, url


def resolved_url_refusal(tool_name: str | None, arguments: dict[str, Any]) -> str | None:
    """Check the URL AFTER step-reference substitution: refusal reason or None."""
    if tool_name != "web_fetch":
        return None
    url = arguments.get("url")
    if not isinstance(url, str) or not url.strip():
        return f"web_fetch url resolved to {type(url).__name__}, not an address"
    reason, _url = _url_refusal(url.strip())
    return f"web_fetch url {reason}" if reason else None


def _sanitize_web_fetch(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Admission for fetching: the URL exists, is http(s), is not a local host and not a placeholder."""
    url = args.get("url")
    if not isinstance(url, str) or not url.strip():
        warnings.append(f"step[{idx}]: web_fetch without url, dropped")
        return None
    # a step reference is a template, not a URL: the check moves to after substitution (`resolved_url_refusal`) with the same locks
    if not has_step_reference(url):
        reason, url = _url_refusal(url)
        if reason:
            warnings.append(f"step[{idx}]: web_fetch url {reason}, dropped")
            return None
    return {
        "tool": "web_fetch",
        "arguments": {"url": url, **_web_fetch_find(args)},
        "label": f"web_fetch:{url[:60]}",
        "expected_outcome": (
            "Fetched page with content_hash + fetched_at; serves as "
            "a verifiable web_page evidence source for the Verifier."
        ),
    }


def _sanitize_lesson_provenance(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Admission for the instrument: the lesson key is a short string or nothing (all lessons)."""
    key = args.get("lesson_key")
    arguments: dict[str, Any] = {}
    if key is not None:
        if not isinstance(key, str) or not key.strip() or len(key) > 64:
            warnings.append(
                f"step[{idx}]: lesson_provenance lesson_key must be a short "
                "string, dropped"
            )
            return None
        arguments["lesson_key"] = key.strip()
    return {
        "tool": "lesson_provenance",
        "arguments": arguments,
        "label": f"lesson_provenance:{arguments.get('lesson_key', 'all')}",
        "expected_outcome": (
            "The receipt chain per lesson (derived_from/injected/acted/"
            "measured) with PROVEN/SELF_DECLARED/ABSENT per link and an "
            "honest verdict."
        ),
    }


def _coerce_int(value: Any) -> int | None:
    """An int the planner meant: 500, 500.0 and "500" all count; "many" does not."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str) and value.strip().lstrip("-").isdigit():
        return int(value.strip())
    return None


def _line_range_arguments(
    args: dict[str, Any], idx: int,
) -> tuple[dict[str, int] | None, list[str]]:
    """file_read's optional window: `{}` when absent, None when malformed.

    Either bound alone is accepted; a reversed pair is a planning error, not something to guess at.
    """
    start_raw = args.get("start_line")
    end_raw = args.get("end_line")
    if start_raw is None and end_raw is None:
        return {}, []
    start = _coerce_int(start_raw) if start_raw is not None else 1
    end = _coerce_int(end_raw) if end_raw is not None else None
    if start is None or (end_raw is not None and end is None):
        return None, [
            (
                f"step[{idx}]: file_read start_line/end_line must be ints, "
                f"got {start_raw!r}/{end_raw!r}, dropped"
            )
        ]
    start = max(1, start)
    if end is None:
        end = start + _DEFAULT_LINE_WINDOW - 1
    if end < start:
        return None, [
            f"step[{idx}]: file_read end_line {end} precedes start_line {start}, dropped"
        ]
    warnings: list[str] = []
    if end - start + 1 > _MAX_LINE_WINDOW:
        warnings.append(
            f"step[{idx}]: file_read window {start}-{end} exceeds "
            f"{_MAX_LINE_WINDOW} lines, clamped"
        )
        end = start + _MAX_LINE_WINDOW - 1
    return {"start_line": start, "end_line": end}, warnings


def _sanitize_patch_check(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """patch_check admission: relative ASCII paths, at most 16 tests, full is a bool."""
    def _rel(p: Any) -> bool:
        return (isinstance(p, str) and bool(p.strip()) and p.isascii()
                and not p.startswith(("/", "\\")) and ":" not in p and ".." not in p)

    path, tests = args.get("path"), args.get("tests") or []
    if not _rel(path):
        warnings.append(f"step[{idx}]: patch_check path must be a relative ASCII path, dropped")
        return None
    if not isinstance(tests, list) or len(tests) > 16 or not all(_rel(t) for t in tests):
        warnings.append(f"step[{idx}]: patch_check tests must be <=16 relative ASCII paths, dropped")
        return None
    cleaned: dict[str, Any] = {"path": path.strip(), "full": bool(args.get("full", False))}
    if tests:
        cleaned["tests"] = [t.strip() for t in tests]
    return {
        "tool": "patch_check", "arguments": cleaned, "label": f"patch_check:{path.strip()[:60]}",
        "expected_outcome": "The patch is applied to a clean copy outside the workspace; "
                            "SEARCH mismatches show real file lines; ruff and pytest output returned.",
    }


def _sanitize_read_logs(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """read_logs admission: last_n clamped to [1..500], filter and trace_id ASCII."""
    requested_n = args.get("last_n", 50)
    event_filter = args.get("event_filter")
    trace_id = args.get("trace_id")
    # clamp, do not drop: a planner that asked for 1000 events wanted the log (same rule as web_search.max_results)
    last_n = _coerce_int(requested_n)
    if last_n is None:
        warnings.append(
            f"step[{idx}]: read_logs last_n must be an int in [1..500], "
            f"got {requested_n!r}, dropped"
        )
        return None
    if not 1 <= last_n <= 500:
        clamped = max(1, min(last_n, 500))
        warnings.append(
            f"step[{idx}]: read_logs last_n {last_n} outside [1..500], "
            f"clamped to {clamped}"
        )
        last_n = clamped
    cleaned_args = {"last_n": last_n}
    if event_filter is not None:
        if not isinstance(event_filter, list):
            warnings.append(
                f"step[{idx}]: read_logs event_filter must be a list, dropped"
            )
            return None
        if len(event_filter) > 20:
            warnings.append(
                f"step[{idx}]: read_logs event_filter too long, dropped"
            )
            return None
        cleaned_filter: list[str] = []
        for j, name in enumerate(event_filter):
            if not isinstance(name, str) or not name.strip():
                warnings.append(
                    f"step[{idx}]: read_logs event_filter[{j}] not a non-empty string, dropped"
                )
                return None
            if not name.isascii():
                warnings.append(
                    f"step[{idx}]: read_logs event_filter[{j}] '{name}' not ASCII, dropped"
                )
                return None
            cleaned_filter.append(name)
        cleaned_args["event_filter"] = cleaned_filter
    if trace_id is not None:
        if not isinstance(trace_id, str) or not trace_id.strip():
            warnings.append(
                f"step[{idx}]: read_logs trace_id must be a non-empty string, dropped"
            )
            return None
        if not trace_id.isascii():
            warnings.append(
                f"step[{idx}]: read_logs trace_id not ASCII, dropped"
            )
            return None
        cleaned_args["trace_id"] = trace_id
    return {
        "tool": "read_logs",
        "arguments": cleaned_args,
        "label": f"read_logs:{trace_id or 'latest'}",
        "expected_outcome": (
            "Returns the last N events from the workspace audit log "
            "(JSONL) for diagnostic review."
        ),
    }


def _sanitize_find_in_files(args: dict[str, Any], idx: int, warnings: list[str]) -> dict[str, Any] | None:
    """Search the workspace: strings as strings, no '..', integer result limit."""
    clean: dict[str, Any] = {}
    for key, default in (("query", ""), ("path", "."), ("name", "*")):
        value = args.get(key, default)
        if not isinstance(value, str):
            warnings.append(f"step[{idx}]: find_in_files {key} must be a string, dropped")
            return None
        clean[key] = value.strip() or default
    if any(seg == ".." for seg in clean["path"].replace("\\", "/").split("/")):
        warnings.append(f"step[{idx}]: find_in_files path contains '..', dropped")
        return None
    clean["regex"] = bool(args.get("regex", False))
    try:
        clean["max_results"] = int(args.get("max_results", 50))
    except (TypeError, ValueError):
        clean["max_results"] = 50
    what = clean["query"] or clean["name"]
    return {
        "tool": "find_in_files",
        "arguments": clean,
        "label": f"find_in_files:{what[:60]}",
        "expected_outcome": "Matching lines as path:line: text, or matching file names; "
                            "a negative answer states how many files were searched.",
    }



def _web_fetch_find(args: dict[str, Any]) -> dict[str, str]:
    """`find` for web_fetch (search within the page): a string up to 200 characters or nothing."""
    find = args.get("find")
    return {"find": find.strip()[:200]} if isinstance(find, str) and find.strip() else {}


def _exists_in_workspace(workspace: str | None, path: str) -> bool:
    """Whether this path exists as a FILE inside the workspace.

    Tells an invented name from the agent's own draft; the path is canonicalised and must stay inside the folder.
    """
    if not workspace or not path:
        return False
    try:
        root = Path(workspace).resolve()
        target = (root / path).resolve()
        target.relative_to(root)
    except (OSError, ValueError):
        return False
    return target.is_file()


#: Self-fix edit file: its content is SEARCH/REPLACE blocks that must repeat another file verbatim, so it can only be read first (`core/patch_route.py`).
_PATCH_FILE_RE = re.compile(r"^proposals/selffix/[^/]+/edits\.txt$")


def _is_patch_file(path: str) -> bool:
    return bool(_PATCH_FILE_RE.match(path.replace("\\", "/").strip().lstrip("./")))


def _patch_task_from(content: str, idx: int, warnings: list[str], path: str) -> str:
    """Turn ready-made patch text into a task to assemble it from what was read.

    The planner cannot know another file's verbatim text; the blocks are assembled just before the write from executed reads
    (`core/write_at_execution.py`). No reads, no text: the step fails honestly. The plan's intent becomes the task content.
    """
    warnings.append(
        f"step[{idx}]: file_write в {path} с готовым текстом — блоки правки "
        "собираются по ПРОЧИТАННОМУ, а не сочиняются планом; заменено заданием")
    return (
        "Собери блоки правки по файлам, прочитанным в этом ходе. Сторона SEARCH "
        "берётся ДОСЛОВНО из вывода чтения — ни одного символа от себя. Если "
        "нужного куска в выводах шагов нет, не выдумывай его: напиши ровно "
        "«нужные строки не прочитаны», и шаг провалится честно. Форма: "
        "FILE:<путь>, затем <<<<<<< SEARCH / ======= / >>>>>>> REPLACE, каждая "
        "метка на своей строке; новый файл — блок с пустым SEARCH. "
        f"Замысел правки, как его задумал план: {content.strip()[:900]}")


def _sanitize_convert_file(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Programs for client files: checks live beside the sandbox (tools/convert_file.py)."""
    from tools.convert_file import sanitize_args
    return sanitize_args(args, idx, warnings)


def _warn_extra_args(args: dict[str, Any], allowed: Any, tool: str, idx: int, warnings: list[str]) -> None:
    if extra := sorted(set(args) - set(allowed)):
        warnings.append(f"step[{idx}]: {tool} dropping unexpected args {extra!r}")


def sanitize_step(
    tool_name: str,
    args: dict[str, Any],
    file_hint: str | None,
    idx: int,
    warnings: list[str],
    *,
    self_documentation_paths: tuple[str, ...] = (),
    workspace: str | None = None,
) -> dict[str, Any] | None:
    if tool_name == "file_read":
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            warnings.append(f"step[{idx}]: file_read without path, dropped")
            return None
        path_norm = path.strip()
        # self-documentation exception: with no --file hint any workspace-relative path may be read; security is enforced by the tool executor (sandbox + secret scanner)
        if not file_hint:
            # No startup --file hint: allow any workspace-relative path.
            pass  # proceed to ASCII check below
        elif path_norm != file_hint.strip():
            # a hint IS provided: only the exact hinted path is allowed
            warnings.append(
                f"step[{idx}]: file_read path '{path}' does not match hint '{file_hint}', "
                "remapping to hinted path"
            )
            path = file_hint
        # ASCII-only identifier policy applies to paths invented by the planner; a user-supplied --file hint may be non-ASCII
        is_explicit_hint_path = bool(file_hint) and path == file_hint.strip()
        if (not path.isascii() and not is_explicit_hint_path
                and not _exists_in_workspace(workspace, path)):
            warnings.append(
                f"step[{idx}]: file_read path '{path}' is not ASCII; "
                "non-ASCII planner-invented identifiers are rejected by policy, dropped"
            )
            return None
        line_args, line_warnings = _line_range_arguments(args, idx)
        if line_warnings:
            warnings.extend(line_warnings)
        if line_args is None:
            return None
        label_range = (
            f":{line_args['start_line']}-{line_args['end_line']}" if line_args else ""
        )
        return {
            "tool": "file_read",
            "arguments": {"path": path, **line_args},
            "label": f"file:{path}{label_range}",
            "expected_outcome": "Non-empty UTF-8 text from the requested file.",
        }

    if tool_name == "web_search":
        query = args.get("query")
        if not isinstance(query, str) or not query.strip():
            warnings.append(f"step[{idx}]: web_search without query, dropped")
            return None
        requested = args.get("max_results", 5)
        try:
            n = int(requested)
        except (TypeError, ValueError):
            warnings.append(f"step[{idx}]: web_search max_results not an int ({requested!r}), defaulting to 5")
            n = 5
        n = max(1, min(n, 10))
        return {
            "tool": "web_search",
            "arguments": {"query": query.strip(), "max_results": n},
            "label": f"web:{query.strip()}",
            "expected_outcome": "List of search hits with url + snippet.",
        }

    if tool_name == "file_write":
        path = args.get("path")
        content = args.get("content")
        # task instead of text: the text is assembled BEFORE the tool call from the outputs of this batch's steps (core/write_at_execution.py)
        instruction = args.get("write_instruction")
        if not isinstance(path, str) or not path.strip():
            warnings.append(f"step[{idx}]: file_write without path, dropped")
            return None
        if not isinstance(content, str) and not (isinstance(instruction, str) and instruction.strip()):
            warnings.append(f"step[{idx}]: file_write content must be a string "
                            "(or give write_instruction), dropped")
            return None
        path = path.strip()
        if isinstance(content, str) and _is_patch_file(path):
            instruction = _patch_task_from(content, idx, warnings, path)
            content = None
        # ASCII-only identifier policy: drop the step before the tool layer raises
        if not path.isascii():
            warnings.append(
                f"step[{idx}]: file_write path '{path}' is not ASCII; "
                "use an English filename (e.g. 'hello.txt'), dropped"
            )
            return None
        # defence in depth: reject obvious sandbox escapes before the tool does, without burning a re-plan slot
        looks_unsafe = (
            path.startswith(("/", "\\"))
            or (len(path) >= 2 and path[1] == ":")  # Windows "C:\..."
            or any(part == ".." for part in path.replace("\\", "/").split("/"))
        )
        if looks_unsafe:
            warnings.append(
                f"step[{idx}]: file_write path '{path}' escapes the workspace, dropped"
            )
            return None
        return {
            "tool": "file_write",
            "arguments": ({"path": path, "content": content} if isinstance(content, str)
                          else {"path": path, "write_instruction": str(instruction).strip()[:2000]}),
            # label uses the path only: content can be huge and sensitive
            "label": f"file_write:{path}",
            "expected_outcome": "File created or overwritten; backup kept on overwrite.",
        }

    if tool_name == "shell_exec":
        argv = args.get("argv")
        if not isinstance(argv, list) or not argv:
            warnings.append(
                f"step[{idx}]: shell_exec requires a non-empty argv list, dropped"
            )
            return None
        if len(argv) > 16:
            warnings.append(
                f"step[{idx}]: shell_exec argv too long ({len(argv)} > 16), dropped"
            )
            return None
        # every element must be a non-empty ASCII string: non-ASCII breaks cmd.exe
        cleaned: list[str] = []
        for j, elem in enumerate(argv):
            if not isinstance(elem, str) or not elem:
                warnings.append(
                    f"step[{idx}]: shell_exec argv[{j}] must be a non-empty "
                    f"string, dropped"
                )
                return None
            if not elem.isascii():
                warnings.append(
                    f"step[{idx}]: shell_exec argv[{j}] '{elem}' is not "
                    f"ASCII; use English-only command names and paths, "
                    f"dropped"
                )
                return None
            cleaned.append(elem)
        # whitelist gate at the planner level; the tool's `_validate_argv` repeats it
        from tools.shell_exec import (
            ALL_WHITELIST,
            MUTATING_COMMANDS,
            READ_ONLY_SUBCOMMANDS,
            WRITE_SUBCOMMANDS,
        )

        cmd = cleaned[0].strip().lower()
        if cmd not in ALL_WHITELIST:
            warnings.append(
                f"step[{idx}]: shell_exec command '{cleaned[0]}' not in "
                f"whitelist, dropped"
            )
            return None
        # subcommand whitelist: both read-only and mutating sets, both from the tool; this must not refuse a permission the tool grants
        sub_allowed = READ_ONLY_SUBCOMMANDS.get(cmd)
        if sub_allowed is not None:
            sub_allowed = sub_allowed | WRITE_SUBCOMMANDS.get(cmd, frozenset())
            if len(cleaned) < 2:
                warnings.append(
                    f"step[{idx}]: shell_exec '{cmd}' requires a "
                    f"subcommand from {sorted(sub_allowed)}, dropped"
                )
                return None
            if cleaned[1].strip().lower() not in sub_allowed:
                warnings.append(
                    f"step[{idx}]: shell_exec '{cmd} {cleaned[1]}' "
                    f"subcommand not in {sorted(sub_allowed)}, dropped"
                )
                return None
        # Shell metacharacters -> drop. No braces: shell=False, and `{{step:` must be searchable. Mirrors `tools.shell_exec._FORBIDDEN_CHARS`.
        _BAD = set(";|&<>`$()[]\n\r\t\0")
        for j, elem in enumerate(cleaned):
            if bad := next((ch for ch in elem if ch in _BAD), None):
                warnings.append(
                    f"step[{idx}]: shell_exec argv[{j}] {elem[:60]!r} contains the shell "
                    f"metacharacter {bad!r} (banned: ; | & < > ` $ ( ) [ ] and newline/CR/tab/NUL), dropped"
                )
                return None
        # Mutating commands must take exactly one safe path argument.
        if cmd in MUTATING_COMMANDS:
            if len(cleaned) != 2:
                warnings.append(
                    f"step[{idx}]: shell_exec '{cmd}' needs exactly one "
                    f"path argument, dropped"
                )
                return None
            path = cleaned[1]
            looks_unsafe = (
                path.startswith(("/", "\\"))
                or (len(path) >= 2 and path[1] == ":")
                or any(p == ".." for p in path.replace("\\", "/").split("/"))
            )
            if looks_unsafe:
                warnings.append(
                    f"step[{idx}]: shell_exec path '{path}' looks unsafe, dropped"
                )
                return None
        return {
            "tool": "shell_exec",
            "arguments": {"argv": cleaned},
            # command name + first arg; for anything longer a 6-hex digest of the WHOLE argv is appended, because the visible part alone is not unique and the loop keys artifacts by label
            "label": _shell_label(cleaned),
            "expected_outcome": (
                "Whitelisted command runs in the workspace sandbox with "
                "a compensation plan; mutating commands escalate to "
                "approval."
            ),
        }

    # ----- python_probe -----
    if tool_name == "python_probe":
        return _sanitize_python_probe(args, idx, warnings)

    if tool_name == "lesson_provenance":
        return _sanitize_lesson_provenance(args, idx, warnings)

    # ----- web_fetch -----
    if tool_name == "web_fetch":
        return _sanitize_web_fetch(args, idx, warnings)

    if tool_name == "rss_fetch":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            warnings.append(f"step[{idx}]: rss_fetch without url, dropped")
            return None
        if len(url) > 2048:
            warnings.append(
                f"step[{idx}]: rss_fetch url too long ({len(url)} > 2048), dropped"
            )
            return None
        if not url.isascii():
            warnings.append(f"step[{idx}]: rss_fetch url not ASCII, dropped")
            return None
        url_lower = url.lower()
        if not (url_lower.startswith(("http://", "https://"))):
            warnings.append(
                f"step[{idx}]: rss_fetch url must start with http:// or https://, dropped"
            )
            return None
        if _is_local_network_host(_url_host(url_lower)):
            warnings.append(
                f"step[{idx}]: rss_fetch url targets local network, dropped"
            )
            return None
        if _is_placeholder_url(url_lower):
            warnings.append(
                f"step[{idx}]: rss_fetch url is a placeholder/example host, dropped"
            )
            return None
        requested = args.get("max_entries", 20)
        try:
            max_entries = int(requested)
        except (TypeError, ValueError):
            warnings.append(
                f"step[{idx}]: rss_fetch max_entries not an int ({requested!r}), defaulting to 20"
            )
            max_entries = 20
        max_entries = max(1, min(max_entries, 50))
        return {
            "tool": "rss_fetch",
            "arguments": {"url": url.strip(), "max_entries": max_entries},
            "label": f"rss_fetch:{url[:60]}",
            "expected_outcome": (
                "Parsed RSS/Atom entries with fetched_at + content_hash; "
                "used as structured feed evidence."
            ),
        }

    # ----- self-repair primitives -----
    if tool_name == "run_tests":
        paths = args.get("paths", ["tests"])
        pattern = args.get("pattern")
        if not isinstance(paths, list):
            warnings.append(
                f"step[{idx}]: run_tests paths must be a list, dropped"
            )
            return None
        if len(paths) > 16:
            warnings.append(
                f"step[{idx}]: run_tests paths too long "
                f"({len(paths)} > 16), dropped"
            )
            return None
        cleaned_paths: list[str] = []
        for j, p in enumerate(paths):
            if not isinstance(p, str) or not p.strip():
                warnings.append(
                    f"step[{idx}]: run_tests paths[{j}] not a non-empty string, dropped"
                )
                return None
            if not p.isascii():
                warnings.append(
                    f"step[{idx}]: run_tests paths[{j}] '{p}' is not ASCII, dropped"
                )
                return None
            if p.startswith(("/", "\\")) or (len(p) >= 2 and p[1] == ":"):
                warnings.append(
                    f"step[{idx}]: run_tests paths[{j}] '{p}' looks absolute, dropped"
                )
                return None
            if any(seg == ".." for seg in p.replace("\\", "/").split("/")):
                warnings.append(
                    f"step[{idx}]: run_tests paths[{j}] '{p}' contains '..', dropped"
                )
                return None
            cleaned_paths.append(p)
        cleaned_args: dict[str, Any] = {"paths": cleaned_paths}
        if pattern is not None:
            if not isinstance(pattern, str):
                warnings.append(
                    f"step[{idx}]: run_tests pattern must be a string, dropped"
                )
                return None
            if len(pattern) > 200:
                warnings.append(
                    f"step[{idx}]: run_tests pattern too long "
                    f"({len(pattern)} > 200), dropped"
                )
                return None
            if not pattern.isascii():
                warnings.append(
                    f"step[{idx}]: run_tests pattern not ASCII, dropped"
                )
                return None
            cleaned_args["pattern"] = pattern
        return {
            "tool": "run_tests",
            "arguments": cleaned_args,
            "label": f"run_tests:{','.join(cleaned_paths)[:60]}",
            "expected_outcome": (
                "Pytest runs in the workspace and returns a structured "
                "summary (passed/failed counts + failed test names)."
            ),
        }

    if tool_name == "patch_check":
        return _sanitize_patch_check(args, idx, warnings)

    if tool_name == "convert_file":
        return _sanitize_convert_file(args, idx, warnings)

    if tool_name == "read_logs":
        return _sanitize_read_logs(args, idx, warnings)

    if tool_name == "diff_file":
        path = args.get("path")
        proposed = args.get("proposed_content")
        ctx_lines = args.get("context_lines", 3)
        if not isinstance(path, str) or not path.strip():
            warnings.append(
                f"step[{idx}]: diff_file without path, dropped"
            )
            return None
        if not path.isascii():
            warnings.append(
                f"step[{idx}]: diff_file path '{path}' is not ASCII, dropped"
            )
            return None
        if path.startswith(("/", "\\")) or (len(path) >= 2 and path[1] == ":"):
            warnings.append(
                f"step[{idx}]: diff_file path '{path}' looks absolute, dropped"
            )
            return None
        if ".." in path.replace("\\", "/").split("/"):
            warnings.append(
                f"step[{idx}]: diff_file path '{path}' contains '..', dropped"
            )
            return None
        if not isinstance(proposed, str):
            warnings.append(
                f"step[{idx}]: diff_file proposed_content must be a string, dropped"
            )
            return None
        if not isinstance(ctx_lines, int) or ctx_lines < 0 or ctx_lines > 20:
            warnings.append(
                f"step[{idx}]: diff_file context_lines must be int in [0..20], dropped"
            )
            return None
        return {
            "tool": "diff_file",
            "arguments": {
                "path": path,
                "proposed_content": proposed,
                "context_lines": ctx_lines,
            },
            # do not echo `proposed_content` in the label (large, may hold secrets)
            "label": f"diff_file:{path}",
            "expected_outcome": (
                "Returns a unified diff between the current workspace "
                "file and the proposed new content; nothing is written."
            ),
        }

    if tool_name == "find_in_files":
        return _sanitize_find_in_files(args, idx, warnings)

    if tool_name == "list_dir":
        path = args.get("path", ".")
        if not isinstance(path, str):
            warnings.append(
                f"step[{idx}]: list_dir path must be a string, dropped"
            )
            return None
        path = path.strip() or "."
        # Reject obvious traversal attempts at the planner level.
        if any(seg == ".." for seg in path.replace("\\", "/").split("/")):
            warnings.append(
                f"step[{idx}]: list_dir path '{path}' contains '..', dropped"
            )
            return None
        return {
            "tool": "list_dir",
            "arguments": {"path": path},
            "label": f"list_dir:{path}",
            "expected_outcome": (
                "Returns a newline-separated list of files and "
                "subdirectories in the specified workspace directory."
            ),
        }

    if tool_name == "current_time":
        # No arguments; ignore anything the planner accidentally adds.
        if args:
            warnings.append(
                f"step[{idx}]: current_time takes no arguments, "
                f"dropping {sorted(args.keys())!r}"
            )
        return {
            "tool": "current_time",
            "arguments": {},
            "label": "current_time:now",
            "expected_outcome": (
                "Returns a dict with iso_utc, iso_local, unix epoch, "
                "tz_name, weekday, year, month, day for the current moment."
            ),
        }

    if tool_name == "model_roster":
        # no arguments; read-only
        if args:
            warnings.append(
                f"step[{idx}]: model_roster takes no arguments, "
                f"dropping {sorted(args.keys())!r}"
            )
        return {
            "tool": "model_roster",
            "arguments": {},
            "label": "model_roster:now",
            "expected_outcome": (
                "Returns a dict with providers (key_present, roles, health, "
                "last_error_class, cost tiers, spend today) and the day cost ceiling."
            ),
        }

    if tool_name == "memory_recall":
        # exactly one string, `term`; read-only
        term = str(args.get("term") or "").strip()
        _warn_extra_args(args, {"term"}, "memory_recall", idx, warnings)
        if not term:
            warnings.append(f"step[{idx}]: memory_recall needs a non-empty term; step dropped")
            return None
        return {
            "tool": "memory_recall",
            "arguments": {"term": term},
            "label": f"memory_recall:{term[:40]}",
            "expected_outcome": (
                "At most 5 of your own past records containing the term, newest "
                "first, each LOW-TRUST with its source; count and more."
            ),
        }

    if tool_name == "model_route":
        # role, provider, reason required; model required unless provider == release; evidence optional
        required = ("role", "provider", "reason")
        missing = [k for k in required if not str(args.get(k) or "").strip()]
        if missing:
            warnings.append(f"step[{idx}]: model_route missing {missing}; step dropped")
            return None
        keep = {k: str(args[k]).strip() for k in ("role", "provider", "model", "reason", "evidence") if args.get(k)}
        _warn_extra_args(args, {*keep, "model", "evidence"}, "model_route", idx, warnings)
        return {
            "tool": "model_route",
            "arguments": keep,
            "label": f"model_route:{keep['role']}->{keep['provider']}",
            "expected_outcome": (
                "set=true with route_id when the decision was stored (journaled as "
                "agent_policy:<id>), or refused_by naming why; the allowed pool."
            ),
        }

    if tool_name == "memory_bank":
        # Contract: exactly text, kind, provenance — all three strings, all required.
        required = ("text", "kind", "provenance")
        missing = [k for k in required if not args.get(k)]
        if missing:
            warnings.append(
                f"step[{idx}]: memory_bank missing required args "
                f"{missing!r}, dropping step"
            )
            return None
        _warn_extra_args(args, required, "memory_bank", idx, warnings)
        kind = args["kind"]
        return {
            "tool": "memory_bank",
            "arguments": {
                "text": args["text"],
                "kind": kind,
                "provenance": args["provenance"],
            },
            "label": f"memory_bank:{kind}",
            "expected_outcome": (
                "Returns a dict with banked (bool), mem_id (str|None), "
                "and kind (str). A None return means the gate refused "
                "the write — that is a gate decision, not an error."
            ),
        }

    if tool_name == "semantic_scholar_search":
        # contract: query required, the rest optional
        query = str(args.get("query") or "").strip()
        if not query:
            warnings.append(
                f"step[{idx}]: semantic_scholar_search needs a non-empty "
                f"query, dropping step"
            )
            return None
        arguments: dict[str, Any] = {"query": query}
        raw_max = args.get("max_results")
        if raw_max is not None:
            try:
                arguments["max_results"] = max(1, min(20, int(raw_max)))
            except (TypeError, ValueError):
                warnings.append(
                    f"step[{idx}]: semantic_scholar_search max_results "
                    f"{raw_max!r} is not a number, ignored"
                )
        fields = args.get("fields_of_study")
        if isinstance(fields, str) and fields.strip():
            arguments["fields_of_study"] = fields.strip()
        _warn_extra_args(args, {"query", "max_results", "fields_of_study"},
                         "semantic_scholar_search", idx, warnings)
        return {
            "tool": "semantic_scholar_search",
            "arguments": arguments,
            "label": f"semantic_scholar_search:{query[:40]}",
            "expected_outcome": (
                "Returns a list of papers with title, url, ar5iv_url, "
                "abstract, year, authors, venue and citation_count. An empty "
                "list means the search found nothing, which is an answer."
            ),
        }

    if tool_name == "journal_append":
        # Contract: exactly path (str) and record (dict) — both required.
        required = ("path", "record")
        missing = [k for k in required if not args.get(k)]
        if missing:
            warnings.append(
                f"step[{idx}]: journal_append missing required args "
                f"{missing!r}, dropping step"
            )
            return None
        # record must be a dict, not a string.
        if not isinstance(args["record"], dict):
            warnings.append(
                f"step[{idx}]: journal_append record must be a dict, "
                f"got {type(args['record']).__name__}, dropping step"
            )
            return None
        _warn_extra_args(args, required, "journal_append", idx, warnings)
        path = args["path"]
        return {
            "tool": "journal_append",
            "arguments": {
                "path": path,
                "record": args["record"],
            },
            "label": f"journal_append:{path}",
            "expected_outcome": (
                "Returns a dict with path (str) and appended (bool). "
                "A ValueError about path boundaries is protection, not a bug."
            ),
        }

    # ----- spawn_subagent: agent-as-tool pattern -----
    if tool_name == "spawn_subagent":
        from core.subagent_runner import _SAFE_SUBAGENT_TOOLS
        from tools.spawn_subagent import (  # local import: avoid cycles
            _MAX_CONTEXT_LEN,
            _MAX_OBJECTIVE_LEN,
            _MAX_ROLE_LEN,
        )

        role = args.get("role")
        objective = args.get("objective")
        context = args.get("context", "")
        allowed_tools_raw = args.get("allowed_tools")
        contract_name_raw = args.get("contract_name")

        if not isinstance(role, str) or not role.strip():
            warnings.append(
                f"step[{idx}]: spawn_subagent requires non-empty 'role', dropped"
            )
            return None
        if len(role) > _MAX_ROLE_LEN:
            warnings.append(
                f"step[{idx}]: spawn_subagent role too long (>{_MAX_ROLE_LEN}), dropped"
            )
            return None

        if not isinstance(objective, str) or not objective.strip():
            warnings.append(
                f"step[{idx}]: spawn_subagent requires non-empty 'objective', dropped"
            )
            return None
        if len(objective) > _MAX_OBJECTIVE_LEN:
            warnings.append(
                f"step[{idx}]: spawn_subagent objective too long (>{_MAX_OBJECTIVE_LEN}), dropped"
            )
            return None

        if not isinstance(context, str):
            context = ""
        if len(context) > _MAX_CONTEXT_LEN:
            warnings.append(
                f"step[{idx}]: spawn_subagent context truncated to {_MAX_CONTEXT_LEN} chars"
            )
            context = context[:_MAX_CONTEXT_LEN]

        reason = subagent_reason(args, idx, warnings)
        if reason is None:
            return None

        # Validate and filter allowed_tools
        cleaned_tools: list[str] | None = None
        if allowed_tools_raw is not None:
            if not isinstance(allowed_tools_raw, list):
                warnings.append(
                    f"step[{idx}]: spawn_subagent allowed_tools must be list or null, ignoring"
                )
            else:
                cleaned_tools = [
                    t for t in allowed_tools_raw
                    if isinstance(t, str) and t in _SAFE_SUBAGENT_TOOLS
                ]
                invalid = [
                    t for t in allowed_tools_raw
                    if not (isinstance(t, str) and t in _SAFE_SUBAGENT_TOOLS)
                ]
                if invalid:
                    warnings.append(
                        f"step[{idx}]: spawn_subagent dropped unsafe/unknown "
                        f"allowed_tools: {invalid!r}"
                    )

        # Resolve contract_name
        if (
            contract_name_raw
            and isinstance(contract_name_raw, str)
            and contract_name_raw.strip()
            and len(contract_name_raw) <= 40
            and contract_name_raw.isascii()
        ):
            contract_name = contract_name_raw.strip()
        else:
            # Slug from role
            contract_name = "".join(
                c if c.isascii() and (c.isalnum() or c in "_-") else "_"
                for c in role
            )[:40].strip("_") or "SubAgent"
            if contract_name_raw is not None:
                warnings.append(
                    f"step[{idx}]: spawn_subagent contract_name invalid, "
                    f"using auto-slug '{contract_name}'"
                )

        clean_args: dict[str, Any] = {
            "role": role.strip(),
            "objective": objective.strip(),
            "context": context,
            "contract_name": contract_name,
            "why": reason[0],
            "expect": reason[1], **expect_sources_arg(args),
        }
        if cleaned_tools is not None:
            clean_args["allowed_tools"] = cleaned_tools

        return {
            "tool": "spawn_subagent",
            "arguments": clean_args,
            "label": f"subagent:{contract_name}",
            "expected_outcome": (
                f"Sub-agent '{contract_name}' (role: {role.strip()}) "
                f"completes its objective and returns its findings."
            ),
        }

    warnings.append(f"step[{idx}]: tool '{tool_name}' has no sanitiser, dropped")
    return None


def fit_resolved_arguments(
    tool_name: str | None, arguments: dict[str, Any],
) -> tuple[dict[str, Any], list[str]]:
    """Re-apply the sanitiser's TRUNCATION rules after step-reference substitution.

    `sanitize_step` sees the plan as a template; the value after substitution is the measured output and has no cap.
    Only truncating rules are mirrored here; a rule that drops a step belongs to admission. Returns the fitted arguments and one warning per rule applied.
    """
    warnings: list[str] = []
    if tool_name == "spawn_subagent":
        from tools.spawn_subagent import _MAX_CONTEXT_LEN  # local import: avoid cycles

        context = arguments.get("context")
        if isinstance(context, str) and len(context) > _MAX_CONTEXT_LEN:
            warnings.append(
                f"spawn_subagent context truncated to {_MAX_CONTEXT_LEN} chars "
                f"after substitution (was {len(context)})"
            )
            arguments = {**arguments, "context": context[:_MAX_CONTEXT_LEN]}
    return arguments, warnings
