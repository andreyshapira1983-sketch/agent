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
import os
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from core.llm import LLM
from tools.base import ToolRegistry


def _build_host_tools_block() -> str:
    """Read tool paths from env vars (loaded from .env) and return a planner
    context block listing what is actually installed on this host.
    Only includes vars that are set and non-empty.
    """
    _ENV_TOOLS = [
        ("BLENDER_PATH",   "blender",   "3D rendering / animation (Blender)"),
        ("OPENSCAD_PATH",  "openscad",  "parametric 3D modelling (OpenSCAD)"),
        ("ADB_PATH",       "adb",       "Android device bridge (ADB)"),
        ("FFMPEG_PATH",    "ffmpeg",    "video / audio processing (FFmpeg)"),
        ("MAGICK_PATH",    "magick",    "image processing (ImageMagick)"),
        ("PANDOC_PATH",    "pandoc",    "document conversion (Pandoc)"),
        ("SOFFICE_PATH",   "soffice",   "office documents (LibreOffice)"),
        ("PYTHON_PATH",    "python",    "Python interpreter (custom path)"),
    ]
    # Also detect Python from common Windows locations if PYTHON_PATH not set
    found: list[str] = []
    for env_var, tool_name, description in _ENV_TOOLS:
        path = os.environ.get(env_var, "").strip()
        if path and os.path.exists(path):
            found.append(f"  {tool_name:12s} → {path}  ({description})")
        elif path:
            # Path configured but file not found at that location
            found.append(f"  {tool_name:12s} → {path}  ({description}) [path configured but not verified]")

    # Auto-detect Python if not already found via PYTHON_PATH
    if not any("python" in line for line in found):
        common_python = [
            r"C:\Users\{}\AppData\Local\Programs\Python\Python311\python.exe".format(os.environ.get("USERNAME", "")),
            r"C:\Users\{}\AppData\Local\Programs\Python\Python312\python.exe".format(os.environ.get("USERNAME", "")),
            r"C:\Python311\python.exe",
            r"C:\Python312\python.exe",
        ]
        for p in common_python:
            if os.path.exists(p):
                found.append(f"  python       → {p}  (Python interpreter, NOT in PATH but installed)")
                break

    if not found:
        return ""

    lines = ["", "Host tools — INSTALLED ON THIS MACHINE (from .env config):"]
    lines += found
    lines += [
        "When the user asks about tasks involving these tools, tell them the tool IS",
        "available at the path shown above. You cannot call these tools directly via",
        "shell_exec (they are outside the workspace sandbox), but you CAN:",
        "  • Write a Python/batch script to the workspace and tell the user to run it",
        "  • Reference the exact path so the user can invoke the tool themselves",
        "  • For Python: write a .py script; user runs it with the Python path above",
        "CRITICAL: When writing any script that uses a tool from this list, ALWAYS",
        "add as the FIRST LINE of the file a comment with the exact run command, e.g.:",
        "  # Run: \"C:\\Program Files\\Blender Foundation\\Blender 5.0\\blender.exe\" --background --python animate_ball.py",
        "  # Run: \"C:\\Users\\andre\\AppData\\Local\\Programs\\Python\\Python311\\python.exe\" script.py",
        "This ensures the user knows the exact command to execute the script.",
    ]
    return "\n".join(lines)


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
    `file_read README.md` and/or `list_dir tools/` without a hint.
    README.md is the overview; `list_dir tools/` shows the ACTUAL tool
    files on disk right now — more reliable than any static doc.
    The sanitiser's self-documentation allowlist permits these paths;
    any other path still requires a hint.

- web_search(query: str, max_results: int) -> list[{title,url,snippet,source}]  [read_only]
    Searches the public web. Use for current events, external facts, or anything
    that requires fresh information you cannot answer from general knowledge.
    Default max_results=5. Maximum 10.
    IMPORTANT: Write the query in the SAME LANGUAGE as the user's question.
    If the user asks in Russian → write the query in Russian.
    If the user asks in English → write the query in English.
    Do NOT translate to English just because it "feels more searchable".

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
    IMPORTANT — environment discovery MANDATORY RULE: when the user asks
    about working with files (PDF, DOCX, images, video, etc.) or asks
    "can you do X / do you have X / is X installed", you MUST probe the
    host with `where` / `which` BEFORE returning any answer. Do NOT skip
    this step — returning steps=[] without probing is WRONG for these questions.
    The FIRST steps in your plan MUST be the `where` checks. Only after
    those can you add a `file_write` step (to write a conversion script etc.).
    Required probes for common tasks:
      PDF/DOCX tasks:  ["where","python"], ["where","soffice"], ["where","pandoc"]
      Image tasks:     ["where","python"], ["where","magick"], ["where","ffmpeg"]
      Video tasks:     ["where","ffmpeg"]
      Python script:   ["where","python"], ["where","pip"]
    On Windows `where` returns the full path if found (exit_code=0) or
    exit_code≠0 if not found. On Linux/macOS use `which` instead.
    The synthesizer will read the `where` results and can tell the user
    exactly what is installed and where — giving a truthful, specific answer.
    NEVER say "unavailable" or "I cannot" without first probing with `where`.
    Each shell_exec is ONE command — plan them as separate steps.
    NEVER use shell metacharacters (; | & < > ` $ ( ) and friends).
    NEVER use absolute paths, drive letters, or '..' — the tool refuses.

- run_tests(paths: list[str] = ["tests"], pattern: str | None = None,
            coverage: bool = False)
    -> {passed, failed, errors, skipped, total, failed_tests, coverage, exit_code, ...}
    [reversible — escalates to human approval; subprocess runs pytest]
    Runs the project's pytest suite (or a filtered subset). Use this
    when the user asks to RUN TESTS, VERIFY, CHECK, or after proposing
    a code change in self-repair mode. `paths` are workspace-relative
    test files or directories (ASCII). `pattern` is a pytest `-k`
    expression (ASCII, <= 200 chars). `coverage=true` adds --cov and
    returns a `coverage` dict with `total_pct` and `worst_covered` list.
    Use coverage=true when the user asks about test ADEQUACY / COVERAGE.
    NEVER use absolute paths or '..'.

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

- list_dir(path: str = ".") -> str  [read_only]
    List files and subdirectories inside a workspace directory.
    Returns one entry per line; directories end with '/'.
    Use this when the user asks "how many files are in X", "what is in
    folder X", "list contents of X", "show files in X", or similar.
    Pass '.' or '' to list the workspace root. NEVER use '..' or absolute
    paths outside the workspace.

- rss_fetch(url: str, max_entries: int)
    -> {url, title, feed_type, entries, fetched_at, content_hash, ...}
    [read_only — no approval needed]
    Fetch ONE RSS/Atom feed and return structured entries. Use only when
    the user gives a feed URL or explicitly asks to inspect an RSS/Atom
    feed. For broad research, prefer `:ingest-rss` / `:ingest-web` from
    the operator command surface rather than inventing feed URLs.

- semantic_scholar_search(query: str, max_results: int, fields_of_study: str)
    -> list[{title, url, ar5iv_url, abstract, year, authors, venue, citation_count}]
    [read_only — no approval needed]
    Search Semantic Scholar for peer-reviewed papers. Returns full metadata
    including abstract and ar5iv_url (direct link to HTML full text on
    ar5iv.labs.arxiv.org). Use this for ANY request involving scientific or
    academic articles. Then follow with web_fetch on the ar5iv_url to get
    the full text. No API key needed.

- spawn_subagent(role: str, objective: str, context: str = "",
                 allowed_tools: list[str] | null = null,
                 contract_name: str | null = null)
    -> str  [read_only — no approval needed]
    Spawn a bounded sub-agent to handle ONE independent parallel sub-task.
    The sub-agent gets its own isolated execution context and its own
    budget (max 3 tool calls, no replanning).

    role          : Who the sub-agent IS — a short specialist label,
                    e.g. "WebResearcher", "FileAnalyst", "CodeReviewer".
    objective     : EXACTLY what the sub-agent must find or return.
                    Be specific — "Find the 3 main limitations of AutoGen
                    as described on the official GitHub README" is good;
                    "research AutoGen" is too vague.
    context       : Optional background from the parent to help the child.
                    Keep it short (< 500 chars). Do NOT repeat the full
                    user question — just what the sub-agent needs.
    allowed_tools : Subset of safe tools the child may use.
                    Allowed values: "file_read", "list_dir", "web_search",
                    "web_fetch", "rss_fetch", "semantic_scholar_search",
                    "run_tests", "read_logs", "diff_file".
                    Set to null to give the child all read-only tools.
    contract_name : Short ASCII identifier for this sub-agent, max 40
                    characters, e.g. "AutoGenResearcher". Optional —
                    defaults to a slug of `role`.

    WHEN TO USE spawn_subagent:
    - The task has 2 or more INDEPENDENT parallel information domains.
    - Example: user asks to compare frameworks A, B, C → spawn one
      ResearchAgent per framework, then synthesise.
    - Example: user asks to analyse a local file AND search for related
      papers → spawn FileAgent + AcademicAgent in parallel.
    - The task is DEEP-tier and has 3+ distinct sub-goals that do not
      depend on each other's results.

    WHEN NOT TO USE spawn_subagent:
    - Simple single-domain questions (always prefer a direct tool call).
    - Sequential tasks where step 2 depends on step 1's output.
    - Anything answerable from general knowledge (use empty steps instead).
    - The user already asked a narrow, well-scoped question.

    HARD LIMITS:
    - Maximum 3 spawn_subagent steps per plan.
    - Sub-agents CANNOT spawn further sub-agents.
    - shell_exec and file_write are NEVER available to sub-agents.
    - Each sub-agent gets at most 3 tool calls total.

    Citation: the sub-agent's answer becomes evidence labelled
    [subagent:<contract_name>] in the Output Contract.

Decision rules:
1. Question is about the hinted file's contents       -> [file_read]
2. Question needs external / current information      -> [web_search, web_fetch <best_url>]
   ALWAYS follow web_search with web_fetch on the most promising URL.
   NEVER stop at web_search alone — web_search gives only snippets (pointers),
   not evidence. The synthesizer needs the actual page text to cite facts.
2b. User wants to READ / FETCH a specific URL         -> [web_fetch <url>]
2c. User asks to find AND read a scientific/academic article:
   -> [semantic_scholar_search <topic in English>,
       web_fetch <ar5iv_url from the best result>]
   ALWAYS use semantic_scholar_search (not web_search) for academic papers.
   The ar5iv_url field in the result is a ready-to-fetch HTML full-text URL.
   Pick the result with the highest citation_count that matches the topic.
   Do NOT fetch arxiv.org directly — it times out. Use ar5iv.labs.arxiv.org.
   Translate/retell in the user's language AFTER fetching the article.
   Search in ENGLISH even if the user asked in Russian — arXiv does not index
   in Russian. Add the translation/retelling step AFTER fetching the article.
3. Question compares the file with the outside world  -> [file_read, web_search, web_fetch]
4. User asked to SAVE / WRITE / STORE to a named file -> [file_write]
5. User asked to RUN a shell command                  -> [shell_exec]
6. User asked to RUN / VERIFY tests                   -> [run_tests]
6b. User asked about test ADEQUACY / COVERAGE / SUFFICIENCY
    (keywords: "хватает ли тестов", "покрывают ли", "достаточно тестов",
    "enough tests", "test coverage", "are all modules tested",
    "coverage report", "покрытие", "какие модули не протестированы")
    -> [list_dir ., list_dir tests/, run_tests(coverage=true)]
    MANDATORY: ALWAYS start with list_dir to see what source modules
    exist, then list_dir tests/ to see what test files exist.
    Without this exploration you CANNOT assess adequacy — you would
    only know the count passed, not whether anything is MISSING.
    run_tests MUST use coverage=true to get the actual coverage %.
    DO NOT omit coverage=true — without it the synthesizer cannot
    report which files are under-tested or what the total % is.
    The synthesizer will compare source modules vs test files and
    highlight gaps from the coverage report.
    EXAMPLE PLAN for "хватает ли тестов":
      step 1: list_dir(path=".")
      step 2: list_dir(path="tests/")
      step 3: run_tests(paths=["tests"], coverage=true)
7. User asked to SHOW logs / errors / "what happened" -> [read_logs]
8. User asked to PREVIEW / DIFF a proposed change     -> [diff_file]
8b. User provided RSS/Atom feed URL to inspect        -> [rss_fetch]
8c. Question asks about folder contents / file count  -> [list_dir]
9. General-knowledge question, no fresh facts needed  -> []  (empty steps)
10. Follow-up that can be answered FROM <conversation_history> alone -> []
    (do NOT re-call a tool to fetch information already present in history)
11. INTROSPECTIVE question about THIS agent itself
12. Task has 2+ INDEPENDENT parallel sub-objectives with different sources
    -> [spawn_subagent(role=..., objective=..., allowed_tools=[...]), ...]
    Examples:
      "Compare AutoGen vs MetaGPT" ->
          [spawn_subagent role=AutoGenResearcher objective="Find AutoGen's key design principles and limitations" allowed_tools=["web_search","web_fetch"],
           spawn_subagent role=MetaGPTResearcher objective="Find MetaGPT's key design principles and limitations" allowed_tools=["web_search","web_fetch"]]
      "Analyze our test suite AND find recent papers on agent testing" ->
          [spawn_subagent role=TestAnalyst objective="Run the test suite and summarise failures" allowed_tools=["run_tests","read_logs"],
           spawn_subagent role=AcademicResearcher objective="Find 2 recent papers on LLM agent testing" allowed_tools=["semantic_scholar_search","web_fetch"]]
    NEVER use spawn_subagent for sequential tasks or simple single-domain questions.
    PREFER direct tool calls when a single domain is sufficient.
    LIMIT: at most 3 spawn_subagent steps per plan.
    ("what do you understand about yourself", "describe your architecture",
    "what tools do you have", "what is your roadmap", "what can you do",
    "as agent", "your safety model", etc.)
    -> [file_read README.md, list_dir tools/]
    README.md gives the architecture overview.
    list_dir tools/ reveals the ACTUAL tool files present on disk right now
    — ground truth that can never go stale. Cite README findings as
    [file:README.md] and tool-dir listings as [file:tools/].

    STRONGER FORM — if the user asks to PROVE capabilities
    ("run your tests", "show me test results", "verify yourself"):
    -> [file_read README.md, list_dir tools/, run_tests]
    run_tests gives live proof of what actually works right now.

    TOOL-LIST SHORTCUT — if the question is ONLY "what tools / инструменты
    do you have?" (no broader architecture question), you already have
    the full tool catalog in your context above. Use [] (empty steps)
    and answer directly from that catalog. No file reads needed.

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

Search query language — STRICT RULE:
  Write `web_search.query` in the SAME LANGUAGE as the user's question.
  If the user asks in Russian  -> query MUST be in Russian.
  If the user asks in English  -> query in English.
  Do NOT translate to English. Do NOT use English keywords for Russian questions.
  WRONG:  question "Найди новости про Python 3.14"  -> query "Python 3.14 news"
  RIGHT:  question "Найди новости про Python 3.14"  -> query "новости Python 3.14"

  EXCEPTION — scientific / academic article search (rule 2c):
  Use semantic_scholar_search with an ENGLISH query regardless of the user's
  language. Semantic Scholar indexes papers in English only.
  WRONG: query="научные статьи о квантовых компьютерах"
  RIGHT: query="quantum computing review" (for semantic_scholar_search)
  After fetching the article via ar5iv_url, translate/retell in the user's language.

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

Windows / tool-capability awareness:
  When the user runs on Windows and asks for something involving desktop
  software, be precise about WHY you cannot do it. Never say "no tools
  available" — always name the specific gap:
  - PDF/DOCX manipulation: requires Microsoft Office (paid subscription)
    OR LibreOffice (free, must be installed) OR python libraries
    (pip install python-docx / pdfplumber). I can WRITE the Python code
    and save it to a file; the user must run it themselves because
    shell_exec does NOT allow running `python`.
  - Rendering 3D / animations: I can write Python code (matplotlib, pygame,
    turtle) and save it; user runs it. I cannot render it myself.
  - GUI apps, audio playback, opening browser windows: genuinely outside
    my capabilities — I can only write the code.
  Always offer the concrete alternative (write the code, name the free tool).

Output format - return ONLY a JSON object, no markdown fences, no preface:
{
  "reasoning": "<1-2 sentences: which rule applies and why>",
  "steps": [
    {
      "tool": "file_read" | "list_dir" | "web_search" | "web_fetch" |
              "semantic_scholar_search" | "rss_fetch" |
              "file_write" | "shell_exec" | "run_tests" |
              "read_logs" | "diff_file" | "spawn_subagent",
      "arguments": { ... },
      "rationale": "<one sentence explaining WHY this step is needed>"
    }
  ]
}

Examples of run_tests arguments:
  Basic run:    {"paths": ["tests"]}
  With filter:  {"paths": ["tests"], "pattern": "test_loop"}
  With coverage (REQUIRED for adequacy/coverage questions):
                {"paths": ["tests"], "coverage": true}

For spawn_subagent steps, arguments must include at least 'role' and 'objective':
  "arguments": {
    "role": "WebResearcher",
    "objective": "Find the 3 main architectural differences between AutoGen and MetaGPT",
    "context": "User asked to compare multi-agent frameworks",
    "allowed_tools": ["web_search", "web_fetch"],
    "contract_name": "AutoGenVsMetaGPT"
  }

If no tools are needed, return: {"reasoning": "...", "steps": []}
"""


@dataclass
class PlannerOutput:
    reasoning: str
    sources: list[dict[str, Any]]
    raw_response: str
    warnings: list[str] = field(default_factory=list)
    # Tool names that the planner requested but were not in the registry.
    # Non-empty means the LLM hallucinated a tool name; the plan was
    # silently down-scoped.  Surfaced via a ``plan_tool_drop`` log event
    # so operators can detect hallucination without digging through raw warnings.
    dropped_tools: list[str] = field(default_factory=list)


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
    DEFAULT_SELF_DOCUMENTATION_PATHS: tuple[str, ...] = ("README.md", "tools/")

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
        llm=None,
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

        `llm` — optional per-call override (adaptive routing). When provided,
        it replaces `self.llm` for this single call only.
        """
        user_prompt = self._build_user_prompt(
            question, file_hint, history, failure_context
        )
        # Kernel-side defense: redact credentials and sensitive PII before
        # either can reach the LLM provider. Clean prompts pass through.
        from core.redaction import redact_dlp_text  # local import: avoid cycles
        safe_prompt, _secret_findings, _pii_findings = redact_dlp_text(user_prompt)
        _active_llm = llm if llm is not None else self.llm
        # Inject dynamic host-tools block so the planner knows what is
        # actually installed on this machine (from .env BLENDER_PATH etc.)
        host_block = _build_host_tools_block()
        effective_system = PLANNER_SYSTEM + host_block if host_block else PLANNER_SYSTEM
        raw = _active_llm.complete(
            system=effective_system,
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

        sources, step_warnings, dropped_tools = self._validate_steps(
            raw_steps, file_hint, forbidden_actions
        )
        # Coverage enforcement: if the question is about test adequacy /
        # coverage and the planner produced a run_tests step without
        # coverage=True, inject it automatically so the synthesizer always
        # gets real coverage data instead of just pass counts.
        _COVERAGE_KEYWORDS = (
            "хватает ли тест", "покрывают ли", "достаточно тест",
            "enough test", "test coverage", "coverage report",
            "покрытие", "не протестирован", "are all modules tested",
        )
        q_lower = question.lower()
        if any(kw in q_lower for kw in _COVERAGE_KEYWORDS):
            for src in sources:
                if src.get("tool") == "run_tests":
                    src.setdefault("arguments", {})
                    if not src["arguments"].get("coverage"):
                        src["arguments"]["coverage"] = True
        return PlannerOutput(
            reasoning=reasoning,
            sources=sources,
            raw_response=raw,
            warnings=parse_warnings + step_warnings,
            dropped_tools=dropped_tools,
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

        from core.task_complexity import needs_live_grounding  # local import: avoid cycles
        if needs_live_grounding(question):
            grounding_block = (
                "\n[LIVE_GROUNDING=required — this question asks about current, "
                "recent, or time-sensitive information. Your plan MUST start with "
                "web_search to retrieve fresh data BEFORE the synthesiser answers. "
                "Do NOT rely on training knowledge alone for facts that change over time.]\n"
            )
        else:
            grounding_block = ""

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
            f"{grounding_block}"
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
    ) -> tuple[list[dict[str, Any]], list[str], list[str]]:
        sources: list[dict[str, Any]] = []
        warnings: list[str] = []
        dropped_tools: list[str] = []

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
                # Track hallucinated tool names separately so the caller can
                # emit a structured log event (plan_tool_drop) without
                # parsing free-text warning strings.
                dropped_tools.append(tool_name)
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

        return sources, warnings, dropped_tools

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
