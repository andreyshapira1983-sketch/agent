"""LLM-driven Planner (§3 Cognitive Core: Planning).

The Planner sees the user's question and a list of available tools, then emits
a JSON plan describing which tools to invoke. It NEVER executes anything —
the Executor (AgentLoop) runs the plan.

Hard rules enforced here:
  - Tools not in the ToolRegistry are dropped.
  - file_read paths that don't match the user-provided hint are dropped
    (so the model cannot wander the workspace on its own).
  - web_search.max_results is clamped to [1, 10].
  - Malformed JSON falls back to an empty plan; the loop then answers from
    general knowledge with explicit "general-knowledge" sourcing.
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.llm import LLM
from tools.base import ToolRegistry


PLANNER_SYSTEM = """You are the planner of an autonomous agent. PLANNER_MODE.

You DO NOT execute tools. You only return a JSON plan that the Executor will run.

Available tools:
- file_read(path: str) -> str  [read_only]
    Reads a UTF-8 text file from inside the workspace.
    Use ONLY when the answer depends on the specific file hinted in the user message.
    NEVER invent paths. If no file hint is given, do NOT call file_read —
    WITH ONE EXCEPTION: for INTROSPECTIVE questions (the user asks "what
    do you understand about yourself / your architecture / your tools /
    your safety / your roadmap / what can you do?"), you MAY call
    `file_read README.md` even without a `--file` hint. README.md
    is the agent's self-documentation and is the single best source
    for these questions. The sanitiser's self-documentation allowlist
    permits this exact path; any other path still requires a hint.

- web_search(query: str, max_results: int) -> list[{title,url,snippet,source}]  [read_only]
    Searches the public web. Use for current events, external facts, or anything
    that requires fresh information you cannot answer from general knowledge.
    Default max_results=5. Maximum 10.

- file_write(path: str, content: str) -> {path, mode, bytes_written, backup_path}
    [reversible if path is new; irreversible if it overwrites — escalates to
     human approval before any overwrite, and keeps a timestamped backup]
    Writes a UTF-8 text file inside the workspace.
    Use ONLY when the user explicitly asks to save / write / create / store
    content to a named file. NEVER write a file just because it might be
    helpful — writes cost trust. NEVER write paths starting with '/', '\\',
    or containing '..' (the tool will refuse anyway). NEVER include any
    credential or API key in `content` (the tool will refuse).

- shell_exec(argv: list[str]) -> {argv, exit_code, stdout, stderr, ...}
    [read_only for whoami/hostname/where/which; irreversible for mkdir/touch
     — escalates to human approval, ships with a compensation plan]
    Runs ONE whitelisted command inside the workspace sandbox.
    Whitelist (the ONLY allowed argv[0] values):
      read-only : whoami, hostname, where, which
      mutating  : mkdir, touch  (exactly one path argument, inside workspace)
    Use ONLY when the user explicitly asks to run a shell command.
    NEVER use shell metacharacters (; | & < > ` $ ( ) and friends).
    NEVER use absolute paths, drive letters, or '..' — the tool refuses.

- run_tests(paths: list[str] = ["tests"], pattern: str | None = None)
    -> {passed, failed, errors, skipped, total, failed_tests, exit_code, ...}
    [reversible — escalates to human approval; subprocess runs pytest]
    Runs the project's pytest suite (or a filtered subset). Use this
    when the user asks to RUN TESTS, VERIFY, CHECK, or after proposing
    a code change in self-repair mode. `paths` are workspace-relative
    test files or directories (ASCII). `pattern` is a pytest `-k`
    expression (ASCII, <= 200 chars). NEVER use absolute paths or '..'.

- read_logs(last_n: int = 50, event_filter: list[str] | None = None,
            trace_id: str | None = None)
    -> {trace_id, log_file, events_returned, total_events, events, ...}
    [read_only — no approval needed]
    Read the agent's own JSONL audit log to diagnose past behaviour.
    Use this when the user asks "what happened", "show logs", "show
    errors", "why did X fail". Without trace_id, reads the
    most-recent log file. `event_filter` example: ["error","replan"].

- diff_file(path: str, proposed_content: str, context_lines: int = 3)
    -> {path, file_exists, diff, additions, deletions, ...}
    [read_only — no approval needed]
    Compute a unified diff between a workspace file and a proposed new
    content. Use this BEFORE calling file_write to show the user what
    would change. `path` ASCII-only, inside workspace.

- web_fetch(url: str)
    -> {url, status_code, content_type, fetched_at, content_hash, text, ...}
    [read_only — no approval needed]
    Fetch ONE web page (http/https only) and return its plain-text
    content with a SHA-256 content_hash and an ISO-8601 fetched_at
    timestamp. Use this AFTER `web_search` to turn a search hit (a
    pointer) into a verifiable source. The Verifier prefers `web_page`
    evidence over `web_search_hit` evidence — so when a user question
    needs an external fact, plan `[web_search, web_fetch <best_url>]`
    instead of `[web_search]` alone. URL must be ASCII, max 2048 chars,
    NOT pointed at localhost / private IPs / metadata endpoints.

- rss_fetch(url: str, max_entries: int)
    -> {url, title, feed_type, entries, fetched_at, content_hash, ...}
    [read_only — no approval needed]
    Fetch ONE RSS/Atom feed and return structured entries. Use only when
    the user gives a feed URL or explicitly asks to inspect an RSS/Atom
    feed. For broad research, prefer `:ingest-rss` / `:ingest-web` from
    the operator command surface rather than inventing feed URLs.

Decision rules:
1. Question is about the hinted file's contents       -> [file_read]
2. Question needs external / current information      -> [web_search]
3. Question compares the file with the outside world  -> [file_read, web_search]
4. User asked to SAVE / WRITE / STORE to a named file -> [file_write]
5. User asked to RUN a shell command                  -> [shell_exec]
6. User asked to RUN / VERIFY tests                   -> [run_tests]
7. User asked to SHOW logs / errors / "what happened" -> [read_logs]
8. User asked to PREVIEW / DIFF a proposed change     -> [diff_file]
8b. User provided RSS/Atom feed URL to inspect        -> [rss_fetch]
9. General-knowledge question, no fresh facts needed  -> []  (empty steps)
10. Follow-up that can be answered FROM <conversation_history> alone -> []
    (do NOT re-call a tool to fetch information already present in history)
11. INTROSPECTIVE question about THIS agent itself
    ("what do you understand about yourself", "describe your architecture",
    "what tools do you have", "what is your roadmap", "what can you do",
    "as agent", "your safety model", etc.)
    -> [file_read README.md]
    README.md is the agent's self-documentation. Cite findings as
    [file:README.md] so the Verifier can resolve them.

ASCII-only identifiers — STRICT RULE:
  File paths, shell argv elements, and tool arguments that name things in
  the codebase MUST be ASCII (A-Z a-z 0-9 . _ - / and similar). The user
  may write to you in Russian, English, or any other language — that is
  FINE for human content. But identifiers are programming, not prose.

  Concrete cases:
    - `file_read.path`, `file_write.path` -> ASCII only.
      If the user says «создай файл привет.txt», plan `path: "hello.txt"`
      (or another sensible English / transliterated filename).
    - `shell_exec.argv` -> every element ASCII only. Names of folders /
      files passed to `mkdir` / `touch` MUST be ASCII.
    - `file_write.content` -> ANY unicode is allowed here (the file body
      is human content — Russian text inside the file is welcome).
    - `web_search.query` -> ANY unicode is allowed here (search terms in
      Russian work great for Russian-language questions).
  The tool layer rejects non-ASCII identifiers anyway; planning around
  this rule saves a retry budget slot.

Re-planning rules — when the user message contains a <replan_context> block,
a previous plan failed. The block lists each failed step with:
  - code: WHY it failed (tool_error, verify_failed, approval_deny,
          approval_abort, approval_unavailable, policy_blocked)
  - tool, arguments: what was attempted
  - reason: human-readable detail

Pick a DIFFERENT approach. Concrete guidance per failure code:
  tool_error          -> change the arguments (different path, different
                         query) or pick a different tool
  verify_failed       -> the tool returned empty / invalid content; try
                         different arguments (e.g. broader web query) or
                         drop the step
  approval_deny       -> a human refused this risk. Propose a SAFER
                         alternative — typically a read-only tool — or
                         return an empty plan and let the synthesizer
                         explain the situation honestly.
  approval_abort      -> same as approval_deny (no input from the human)
  approval_unavailable-> the system has no approval channel wired. Same
                         response: pick a read-only path or empty plan.
  policy_blocked      -> the tool you picked is not registered / not
                         allowed. Pick a registered tool, or empty plan.

Hard ceiling: never repeat an EXACT (tool, arguments) pair that already
appears in the failure list — the kernel limits total attempts and
copying a failed step wastes a retry slot. When no safer alternative
exists, returning `"steps": []` is a valid, honest plan.

Output format - return ONLY a JSON object, no markdown fences, no preface:
{
  "reasoning": "<1-2 sentences: which rule applies and why>",
  "steps": [
    {
      "tool": "file_read" | "web_search" | "file_write" | "shell_exec" |
              "run_tests" | "read_logs" | "diff_file" | "web_fetch" |
              "rss_fetch",
      "arguments": { ... },
      "rationale": "<one sentence>"
    }
  ]
}

If no tools are needed, return: {"reasoning": "...", "steps": []}
"""


@dataclass
class PlannerOutput:
    reasoning: str
    sources: list[dict[str, Any]]
    raw_response: str
    warnings: list[str] = field(default_factory=list)


class LLMPlanner:
    """Asks the LLM to choose tools. Validates and sanitises the result."""

    # Self-documentation files the planner may read EVEN WITHOUT a
    # `--file hint`. The allowlist is intentionally tiny:
    #   * narrow scope (just project documentation),
    #   * ASCII-only paths (so the existing identifier policy doesn't
    #     fight us),
    #   * read-only operation,
    #   * answers exactly the "introspection" use case that motivated
    #     this exception (see MVP-14.4.x notes).
    # Any other file requires the user to pass `--file <path>`.
    DEFAULT_SELF_DOCUMENTATION_PATHS: tuple[str, ...] = ("README.md",)

    def __init__(
        self,
        llm: LLM,
        registry: ToolRegistry,
        self_documentation_paths: tuple[str, ...] | None = None,
    ):
        self.llm = llm
        self.registry = registry
        # Defensive copy + validation: every entry must be a relative
        # ASCII path with no traversal. If the caller passes garbage,
        # we fall back to the default rather than crashing.
        if self_documentation_paths is None:
            self.self_documentation_paths = self.DEFAULT_SELF_DOCUMENTATION_PATHS
        else:
            clean: list[str] = []
            for p in self_documentation_paths:
                if (
                    isinstance(p, str)
                    and p.strip()
                    and p.isascii()
                    and ".." not in p
                    and not p.startswith(("/", "\\"))
                    and ":" not in p
                ):
                    clean.append(p.strip())
            self.self_documentation_paths = tuple(clean)

    def plan(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> PlannerOutput:
        """Ask the LLM for a plan.

        `failure_context` is the formatted `<replan_context>` block built by
        `AgentLoop` from previous attempts' `ReplanTrigger`s. Empty on the
        first attempt; non-empty on every replan. The block sits AFTER
        conversation history and IMMEDIATELY BEFORE the question so the
        model reads the failure right before it decides what to try.

        `forbidden_actions` (MVP-12) is a tuple of (tool, args_json) pairs
        the sanitiser must REJECT. Populated by `ReplanPolicy` for
        failures whose budget has `requires_different_action=True`
        (approval_deny, policy_blocked, etc.).
        """
        user_prompt = self._build_user_prompt(
            question, file_hint, history, failure_context
        )
        # Kernel-side defense: redact any credential shape before it can
        # reach the LLM provider. If the prompt cleanly contains no
        # secrets, redaction is a no-op.
        from core.redaction import redact_text  # local import: avoid cycles
        safe_prompt, _findings = redact_text(user_prompt)
        raw = self.llm.complete(
            system=PLANNER_SYSTEM,
            user=safe_prompt,
            max_tokens=1024,
            temperature=0.0,
        )
        parsed, parse_warnings = self._parse_json(raw)
        if parsed is None:
            return PlannerOutput(
                reasoning="(planner output did not parse — falling back to empty plan)",
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["plan_parse_failed"],
            )

        reasoning = str(parsed.get("reasoning", "")).strip() or "(no reasoning provided)"
        raw_steps = parsed.get("steps") or []
        if not isinstance(raw_steps, list):
            return PlannerOutput(
                reasoning=reasoning,
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["steps_field_not_a_list"],
            )

        sources, step_warnings = self._validate_steps(
            raw_steps, file_hint, forbidden_actions
        )
        return PlannerOutput(
            reasoning=reasoning,
            sources=sources,
            raw_response=raw,
            warnings=parse_warnings + step_warnings,
        )

    # ---------- prompt construction ----------

    def _build_user_prompt(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
    ) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hint = file_hint if file_hint else "(none)"
        tool_names = ", ".join(t.name for t in self.registry.list())

        history_block = (
            f"<conversation_history>\n{history}\n</conversation_history>\n\n"
            if history.strip()
            else ""
        )
        # Replan context sits between history and question — close enough
        # to the question to be salient, but separated from old turns so
        # the model doesn't confuse "what I tried this cycle" with "what I
        # discussed in a prior turn".
        replan_block = (
            f"{failure_context.rstrip()}\n\n" if failure_context.strip() else ""
        )

        return (
            f"current_date: {today}\n"
            f"file hint: {hint}\n"
            f"registered tools: {tool_names}\n"
            f"\n"
            f"{history_block}"
            f"{replan_block}"
            f"question: {question}\n"
            f"\n"
            f"Return your JSON plan now."
        )

    # ---------- JSON parsing ----------

    @staticmethod
    def _parse_json(raw: str) -> tuple[dict[str, Any] | None, list[str]]:
        warnings: list[str] = []
        text = raw.strip()

        # Strip a leading ```json or ``` fence if present.
        fence = re.match(r"^```(?:json)?\s*(.*?)\s*```\s*$", text, flags=re.DOTALL)
        if fence:
            text = fence.group(1).strip()
            warnings.append("stripped_markdown_fence")

        # Direct parse first.
        try:
            obj = json.loads(text)
            if isinstance(obj, dict):
                return obj, warnings
            warnings.append("top_level_not_object")
        except json.JSONDecodeError:
            pass

        # Fallback: find first '{' and matching last '}'.
        start = text.find("{")
        end = text.rfind("}")
        if start != -1 and end != -1 and end > start:
            try:
                obj = json.loads(text[start : end + 1])
                if isinstance(obj, dict):
                    warnings.append("extracted_json_substring")
                    return obj, warnings
            except json.JSONDecodeError:
                pass

        warnings.append("json_decode_error")
        return None, warnings

    # ---------- step validation ----------

    def _validate_steps(
        self,
        raw_steps: list[Any],
        file_hint: str | None,
        forbidden_actions: tuple[tuple[str, str], ...] = (),
    ) -> tuple[list[dict[str, Any]], list[str]]:
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []

        forbidden_set: set[tuple[str, str]] = set(forbidden_actions)

        for idx, step in enumerate(raw_steps):
            if not isinstance(step, dict):
                warnings.append(f"step[{idx}]: not an object, dropped")
                continue

            tool_name = step.get("tool")
            args = step.get("arguments") or {}
            if not isinstance(tool_name, str) or not isinstance(args, dict):
                warnings.append(f"step[{idx}]: missing tool name or arguments, dropped")
                continue

            # Unknown tool -> drop (do not let the planner widen the surface).
            try:
                self.registry.get(tool_name)
            except KeyError:
                warnings.append(f"step[{idx}]: tool '{tool_name}' not registered, dropped")
                continue

            # MVP-12 forbidden-action gate. If the ReplanPolicy marked an
            # earlier (tool, args) pair as no-retry (approval_deny,
            # policy_blocked, etc.), the planner is not allowed to revive
            # it even if the LLM tries again. Canonicalise args the same
            # way ReplanPolicy did (sorted JSON keys).
            try:
                canonical_args = json.dumps(args, sort_keys=True, ensure_ascii=False)
            except TypeError:
                canonical_args = ""
            if canonical_args and (tool_name, canonical_args) in forbidden_set:
                warnings.append(
                    f"step[{idx}]: ({tool_name}, {canonical_args}) is in the "
                    f"forbidden_actions list from a prior failure, dropped"
                )
                continue

            spec = self._sanitize_step(
                tool_name, args, file_hint, idx, warnings,
                self_documentation_paths=self.self_documentation_paths,
            )
            if spec is None:
                continue
            sources.append(spec)

        return sources, warnings

    @staticmethod
    def _sanitize_step(
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
            is_self_doc = path_norm in self_documentation_paths
            if not file_hint:
                if not is_self_doc:
                    warnings.append(
                        f"step[{idx}]: file_read requested but no --file hint was "
                        f"provided and '{path_norm}' is not in the self-documentation "
                        f"allowlist {list(self_documentation_paths)}, dropped"
                    )
                    return None
                # Self-doc read with no hint: leave `path` as the LLM
                # asked, skip the hint-equality check below.
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
            from tools.shell_exec import ALL_WHITELIST, MUTATING_COMMANDS

            cmd = cleaned[0].strip().lower()
            if cmd not in ALL_WHITELIST:
                warnings.append(
                    f"step[{idx}]: shell_exec command '{cleaned[0]}' not in "
                    f"whitelist, dropped"
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

        warnings.append(f"step[{idx}]: tool '{tool_name}' has no sanitiser, dropped")
        return None
