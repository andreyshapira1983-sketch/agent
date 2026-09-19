"""Admission rules for one planner step — the whitelist the model cannot argue
with.
"""
from __future__ import annotations

import hashlib
import ipaddress
from typing import Any

_PLACEHOLDER_HOSTS = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "www.example.com", "www.example.org", "www.example.net", "www.example.edu",
})

_PLACEHOLDER_TLDS = (".example", ".invalid", ".test", ".localhost")


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


#: file_read window defaults. A window is asked for when the planner already
#: knows WHERE to look, so it is short: enough for a function, not a file.
_DEFAULT_LINE_WINDOW = 60
_MAX_LINE_WINDOW = 400



# How much of argv[1] may appear in a label. The digest carries uniqueness,
# so the visible part exists only to stay readable — an unbounded token would
# put a data blob (or a value the caller passed as the first argument) into
# every journal line that quotes the label.
_LABEL_ARG_CHARS = 32


def _shell_label(argv: list[str]) -> str:
    """Short, collision-resistant label for one shell_exec step.

    Lossless while it can be: with at most two tokens AND argv[1] within
    ``_LABEL_ARG_CHARS`` the label already contains the whole command, so no
    digest is needed. The moment anything visible is dropped — a third token
    or a truncated argv[1] — the whole argv is folded into a 24-bit digest:
    collisions are astronomically unlikely per run, not impossible, and the
    cost of one would be the pre-existing overwrite, never a wrong answer.
    """
    cmd = argv[0].strip().lower() if argv else ""
    head = f"shell_exec:{cmd}"
    truncated = False
    if len(argv) > 1:
        arg = argv[1]
        truncated = len(arg) > _LABEL_ARG_CHARS
        head += f" {arg[:_LABEL_ARG_CHARS]}…" if truncated else f" {arg}"
    if len(argv) <= 2 and not truncated:
        # Only here is the label still the whole command; a truncated
        # argv[1] is lossy, so it falls through to the digest below —
        # otherwise two commands sharing the visible prefix collide, which
        # is the exact overwrite the digest exists to prevent.
        return head
    digest = hashlib.blake2s(
        repr(argv).encode("utf-8", "replace"), digest_size=3
    ).hexdigest()
    return f"{head} #{digest}"


#: Потолок кода эксперимента: замер — не программа (tools/python_probe.py).
_PROBE_CODE_CAP = 4000


def _sanitize_python_probe(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Пропуск лаборатории: форма шага. Глубину (AST-гейт эффектов, контейнер)
    судит сам инструмент — здесь только «код есть, размер и таймаут вменяемы».
    Живой повод 2026-08-16 (проба №3): планировщик, выучив карту, спланировал
    эксперимент — и шаг умер с «no sanitiser, dropped». У руки не было пропуска.
    """
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
    inputs = args.get("inputs")  # файлы рабочей папки для счёта (2026-09-19)
    if inputs is not None:
        if not (isinstance(inputs, list) and inputs
                and all(isinstance(p, str) and p.strip() for p in inputs)):
            warnings.append(f"step[{idx}]: python_probe inputs must be a list of paths, dropped")
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


def _sanitize_lesson_provenance(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """Пропуск прибора: ключ урока — короткая строка или ничего (все уроки)."""
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

    Without a window `file_read` returns the whole file and the evidence
    budget keeps a 12 000-char excerpt chosen by keyword — so a planner that
    knew the line number (from findstr) still could not read that line
    (measured 2026-09-05, three turns in a row). Either bound alone is
    accepted; a reversed pair is a planning error, not something to guess at.
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


def _sanitize_read_logs(
    args: dict[str, Any], idx: int, warnings: list[str],
) -> dict[str, Any] | None:
    """read_logs admission: last_n clamped to [1..500], filter and trace_id ASCII."""
    requested_n = args.get("last_n", 50)
    event_filter = args.get("event_filter")
    trace_id = args.get("trace_id")
    # Clamp, do not drop: a planner that asked for 1000 events wanted the
    # log, not nothing. Three read_logs steps were deleted this way in one
    # exam session (2026-09-05) and the agent read a neighbour's trace
    # instead. web_search.max_results has always been clamped; same rule.
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
    """Поиск по рабочей папке: строки как строки, без '..', целый предел выдачи."""
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
    """`find` для web_fetch (поиск по странице, 2026-09-20): строка до 200 знаков или ничего."""
    find = args.get("find")
    return {"find": find.strip()[:200]} if isinstance(find, str) and find.strip() else {}


def sanitize_step(
    tool_name: str,
    args: dict[str, Any],
    file_hint: str | None,
    idx: int,
    warnings: list[str],
    *,
    self_documentation_paths: tuple[str, ...] = (),
) -> dict[str, Any] | None:
    if tool_name == "file_read":
        path = args.get("path")
        if not isinstance(path, str) or not path.strip():
            warnings.append(f"step[{idx}]: file_read without path, dropped")
            return None
        path_norm = path.strip()
        # MVP-14.4.x — narrow exception for self-documentation
        # (e.g. README.md). The user asks "what do you understand
        # about yourself?" → planner should reach for the docs
        # without needing a --file hint. The allowlist is tiny
        # and enforced both here and at instantiation time.
        # MVP-14.4.x — self-documentation allowlist is kept for reference
        # but file_read is now allowed for any workspace-relative path when
        # no --file hint is given. Security is enforced by the tool executor
        # (workspace sandbox + secret scanner). The allowlist is no longer
        # used to gate reads.
        if not file_hint:
            # No startup --file hint: allow any workspace-relative path.
            pass  # proceed to ASCII check below
        elif path_norm != file_hint.strip():
            # A hint IS provided — only the exact hinted path is
            # allowed (the existing pre-MVP-14 contract).
            warnings.append(
                f"step[{idx}]: file_read path '{path}' does not match hint '{file_hint}', "
                "remapping to hinted path"
            )
            path = file_hint
        # ASCII-only identifier policy applies to paths invented by
        # the planner. A user-supplied --file hint is explicit CLI
        # input, so non-ASCII filenames (e.g. Russian documents) are
        # allowed after the hint-equality/remap gate above.
        is_explicit_hint_path = bool(file_hint) and path == file_hint.strip()
        if not path.isascii() and not is_explicit_hint_path:
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
        if not isinstance(path, str) or not path.strip():
            warnings.append(f"step[{idx}]: file_write without path, dropped")
            return None
        if not isinstance(content, str):
            warnings.append(
                f"step[{idx}]: file_write content must be a string, dropped"
            )
            return None
        path = path.strip()
        # ASCII-only identifier policy. Catches the LLM trying to
        # honour a literal user request like «создай файл привет.txt»
        # — the planner should transliterate, but if it doesn't, we
        # drop the step before the tool layer raises.
        if not path.isascii():
            warnings.append(
                f"step[{idx}]: file_write path '{path}' is not ASCII; "
                "use an English filename (e.g. 'hello.txt'), dropped"
            )
            return None
        # Defence in depth: reject obvious sandbox escapes BEFORE the
        # tool would refuse them. The tool still validates the
        # canonical path, but this catches the common mistakes
        # without burning a re-plan slot.
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
            "arguments": {"path": path, "content": content},
            # Label uses the path only — content can be huge and is also
            # the thing most likely to carry sensitive data.
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
        # Every element must be a non-empty ASCII string. Shell
        # argv is a programming boundary — non-ASCII breaks cmd.exe
        # and would not be a legitimate command name or path here.
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
        # Whitelist gate at the planner level — keeps obviously
        # dangerous commands out of the JSONL before the tool even
        # sees them. The tool's `_validate_argv` repeats the check
        # (defence in depth).
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
        # Subcommand whitelist. Both sets, and both from the tool: this
        # sanitizer used to consult the read-only half alone, so a planner
        # that correctly planned `git checkout -b` / `add` / `commit` had
        # those three steps deleted here — silently, as warnings — and the
        # run then reported that it could not commit. The tool's own
        # `_validate_argv` still re-checks every shape (defence in depth);
        # what this must not do is refuse a permission the tool grants.
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
        # Shell metacharacters -> drop. No braces: shell=False, and `{{step:` must be searchable (exam 2026-09-05, turns 35–36). Mirrors `tools.shell_exec._FORBIDDEN_CHARS`.
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
            # Label is the command name + first arg — short, and it never
            # echoes long argv. For anything longer a 6-hex digest of the
            # WHOLE argv is appended, because the visible part alone is not
            # unique: `grep -rl A core` and `grep -rl B core` both rendered
            # as "shell_exec:grep -rl", and the loop keys its artifacts by
            # label, so the second silently overwrote the first and the
            # synthesizer never saw the answer it had already fetched
            # (measured twice against the live agent, 2026-08-01).
            "label": _shell_label(cleaned),
            "expected_outcome": (
                "Whitelisted command runs in the workspace sandbox with "
                "a compensation plan; mutating commands escalate to "
                "approval."
            ),
        }

    # ----- лаборатория (2026-08-16) -----
    if tool_name == "python_probe":
        return _sanitize_python_probe(args, idx, warnings)

    if tool_name == "lesson_provenance":
        return _sanitize_lesson_provenance(args, idx, warnings)

    # ----- MVP-14.2 web_fetch -----
    if tool_name == "web_fetch":
        url = args.get("url")
        if not isinstance(url, str) or not url.strip():
            warnings.append(
                f"step[{idx}]: web_fetch without url, dropped"
            )
            return None
        if len(url) > 2048:
            warnings.append(
                f"step[{idx}]: web_fetch url too long ({len(url)} > 2048), dropped"
            )
            return None
        if not url.isascii():
            warnings.append(
                f"step[{idx}]: web_fetch url not ASCII, dropped"
            )
            return None
        url_lower = url.lower()
        if not (url_lower.startswith(("http://", "https://"))):
            warnings.append(
                f"step[{idx}]: web_fetch url must start with http:// or https://, dropped"
            )
            return None
        # Block obvious SSRF shapes BEFORE the tool layer.
        if _is_local_network_host(_url_host(url_lower)):
            warnings.append(
                f"step[{idx}]: web_fetch url targets local network, dropped"
            )
            return None
        if _is_placeholder_url(url_lower):
            warnings.append(
                f"step[{idx}]: web_fetch url is a placeholder/example host, dropped"
            )
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

    # ----- MVP-13.1 self-repair primitives -----
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
            # Don't echo `proposed_content` in the label (it can be
            # huge and may contain secrets — the tool layer redacts
            # the diff, but a label is a separate surface).
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
        if path.startswith(("/", "\\")) or (len(path) >= 2 and path[1] == ":"):
            # Absolute paths are validated by the tool; pass them through
            # so the tool can give a clear PermissionError.
            pass
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
        # The eye on his own models (2026-09-04): no arguments; read-only.
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
        # The read door (2026-09-04): exactly one string, `term`; read-only.
        term = str(args.get("term") or "").strip()
        extra = sorted(set(args) - {"term"})
        if extra:
            warnings.append(f"step[{idx}]: memory_recall dropping unexpected args {extra!r}")
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
        # His routing door (2026-09-04 22:20): role, provider, reason required;
        # model required unless provider == release; evidence optional.
        required = ("role", "provider", "reason")
        missing = [k for k in required if not str(args.get(k) or "").strip()]
        if missing:
            warnings.append(f"step[{idx}]: model_route missing {missing}; step dropped")
            return None
        keep = {k: str(args[k]).strip() for k in ("role", "provider", "model", "reason", "evidence") if args.get(k)}
        extra = sorted(set(args) - set(keep) - {"model", "evidence"})
        if extra:
            warnings.append(f"step[{idx}]: model_route dropping unexpected args {extra!r}")
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
        # Drop any extra arguments the planner accidentally adds.
        extra = sorted(set(args.keys()) - set(required))
        if extra:
            warnings.append(
                f"step[{idx}]: memory_bank dropping unexpected args {extra!r}"
            )
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
        # Найден сторожем «зарегистрирован, но мёртв» 2026-09-01: инструмент
        # стоял в поясе и в промпте планировщика, а ветки не имел — любой шаг
        # с ним выбрасывался молча, и поиск научных работ был мёртвой
        # способностью. Контракт: query обязателен, остальное необязательно.
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
        extra = sorted(set(args) - {"query", "max_results", "fields_of_study"})
        if extra:
            warnings.append(
                f"step[{idx}]: semantic_scholar_search dropping unexpected "
                f"args {extra!r}"
            )
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
        # Drop any extra arguments the planner accidentally adds.
        extra = sorted(set(args.keys()) - set(required))
        if extra:
            warnings.append(
                f"step[{idx}]: journal_append dropping unexpected args {extra!r}"
            )
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

    `sanitize_step` sees the plan as a template: a `context` of
    ``{{step:3.output}}`` is 18 characters and passes. The value that
    arrives after substitution is the measured output, and it has no cap.
    Measured 2026-09-05 (exam, session `exam_h2`): a page from pytest's docs
    plus a `findstr` listing landed in a sub-agent's `context`, the tool
    refused it («'context' exceeds 2000 characters»), the step failed, and
    the three steps that depended on it were never executed — the run that
    was supposed to repair the agent ended without a single file written.

    Only the rules that TRUNCATE are mirrored here; a rule that drops a step
    belongs to admission, and a step that was admitted is not re-judged.
    Returns the fitted arguments and the warnings, one per rule applied.
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
