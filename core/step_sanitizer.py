"""Admission rules for one planner step — the whitelist the model cannot argue with.

Moved out of ``core/planner.py`` (piece 1 of its decomposition, 2026-08-02):
a 665-line pure staticmethod was a quarter of the planner file while touching
none of the planner's state. Every rule here answers the same question — is
this single proposed step safe and well-formed enough to admit? — and the
answer never depends on the LLM, the registry object, or any instance.

The placeholder-URL vocabulary moves with it: both of its call sites were
inside this function.
"""
from __future__ import annotations

from typing import Any


_PLACEHOLDER_HOSTS = frozenset({
    "example.com", "example.org", "example.net", "example.edu",
    "www.example.com", "www.example.org", "www.example.net", "www.example.edu",
})

_PLACEHOLDER_TLDS = (".example", ".invalid", ".test", ".localhost")


def _url_host(url_lower: str) -> str:
    """Extract the bare host from an already http/https, ASCII, lowercased URL."""
    after_scheme = url_lower.split("://", 1)[-1]
    host = after_scheme.split("/", 1)[0]
    host = host.split("@", 1)[-1]   # strip any userinfo
    host = host.split(":", 1)[0]    # strip port
    return host.strip(".")


def _is_placeholder_url(url_lower: str) -> bool:
    """True for reserved documentation/example/test hosts (never real targets)."""
    host = _url_host(url_lower)
    if not host:
        return False
    if host in _PLACEHOLDER_HOSTS:
        return True
    return any(host.endswith(tld) for tld in _PLACEHOLDER_TLDS)



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
        return {
            "tool": "file_read",
            "arguments": {"path": path},
            "label": f"file:{path}",
            "expected_outcome": "Non-empty UTF-8 text from the hinted file.",
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
            or len(path) >= 2 and path[1] == ":"  # Windows "C:\..."
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
        # Shell metacharacters anywhere -> drop.
        _BAD = set(";|&<>`$()[]{}\n\r\t\0")
        for j, elem in enumerate(cleaned):
            if any(ch in _BAD for ch in elem):
                warnings.append(
                    f"step[{idx}]: shell_exec argv[{j}] contains a "
                    f"shell metacharacter, dropped"
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
                or len(path) >= 2 and path[1] == ":"
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
            # Label is just the command name + first arg if any — keeps
            # the planner JSONL short and never echoes long argv.
            "label": (
                f"shell_exec:{cmd}"
                + (f" {cleaned[1]}" if len(cleaned) > 1 else "")
            ),
            "expected_outcome": (
                "Whitelisted command runs in the workspace sandbox with "
                "a compensation plan; mutating commands escalate to "
                "approval."
            ),
        }

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
        if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
            warnings.append(
                f"step[{idx}]: web_fetch url must start with http:// or https://, dropped"
            )
            return None
        # Block obvious SSRF shapes BEFORE the tool layer.
        for blocked in (
            "://localhost", "://127.", "://0.0.0.0",
            "://10.", "://192.168.", "://169.254.",
            "://[::1]",
        ):
            if blocked in url_lower:
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
            "arguments": {"url": url},
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
        if not (url_lower.startswith("http://") or url_lower.startswith("https://")):
            warnings.append(
                f"step[{idx}]: rss_fetch url must start with http:// or https://, dropped"
            )
            return None
        for blocked in (
            "://localhost", "://127.", "://0.0.0.0",
            "://10.", "://192.168.", "://169.254.",
            "://[::1]",
        ):
            if blocked in url_lower:
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
        last_n = args.get("last_n", 50)
        event_filter = args.get("event_filter")
        trace_id = args.get("trace_id")
        if not isinstance(last_n, int) or last_n < 1 or last_n > 500:
            warnings.append(
                f"step[{idx}]: read_logs last_n must be an int in [1..500], dropped"
            )
            return None
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

    # ----- spawn_subagent: agent-as-tool pattern -----
    if tool_name == "spawn_subagent":
        from tools.spawn_subagent import (  # local import: avoid cycles
            _MAX_CONTEXT_LEN,
            _MAX_OBJECTIVE_LEN,
            _MAX_ROLE_LEN,
        )
        from core.subagent_runner import _SAFE_SUBAGENT_TOOLS  # noqa: PLC0415

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
