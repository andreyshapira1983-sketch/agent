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
from core.step_sanitizer import sanitize_step
from core.doc_routing import (
    _drop_readme_status_sources,
    _drop_web_lookup_for_introspection,
    _ensure_confidence_evidence_sources_first,
    _ensure_doctrine_docs_first,
    _ensure_memory_governance_docs_first,
    _ensure_self_repair_doctrine_docs_first,
    _ensure_subagent_governance_docs_first,
    _explicitly_requests_readme,
    _is_confidence_evidence_diagnostic_question,
    _is_doctrine_corporate_question,
    _is_memory_governance_question,
    _is_self_repair_doctrine_question,
    _is_self_repo_introspection_question,
    _is_subagent_governance_question,
    _requests_implementation_detail,
    _should_prefer_memory_over_readme,
)
from tools.base import ToolRegistry


# Output budget for a single planning call.
#
# The previous hard-coded 1024 was too tight for a multi-step JSON plan, and on
# OpenAI reasoning models it was actively harmful: `max_completion_tokens`
# covers internal reasoning first, so the entire budget could be spent thinking
# and the API would return success with an EMPTY answer. The run log showed the
# planner burning 1024 + 1024 tokens across an auto-continue round and parsing
# zero characters both times.
#
# `core.llm` raises this further for reasoning models (see
# `_REASONING_TOKEN_FLOOR`); the two floors compose, neither lowers the other.
_PLAN_MAX_TOKENS_DEFAULT = 2048


def _plan_max_tokens() -> int:
    """Per-call planner output budget, overridable via ``AGENT_PLAN_MAX_TOKENS``.

    Falls back to the default when the variable is missing, non-numeric, or
    non-positive, so a malformed environment can never produce a zero budget
    (which would guarantee an empty plan on every run).
    """
    raw = os.getenv("AGENT_PLAN_MAX_TOKENS")
    if raw is None:
        return _PLAN_MAX_TOKENS_DEFAULT
    try:
        value = int(raw.strip())
    except (TypeError, ValueError):
        return _PLAN_MAX_TOKENS_DEFAULT
    return value if value > 0 else _PLAN_MAX_TOKENS_DEFAULT




# Single source of truth for host-tool names (env var, tool name, description).
# Reused by `_build_host_tools_block` and `host_tools_relevant` so the two never
# drift apart.
_HOST_ENV_TOOLS = [
    ("BLENDER_PATH",   "blender",   "3D rendering / animation (Blender)"),
    ("OPENSCAD_PATH",  "openscad",  "parametric 3D modelling (OpenSCAD)"),
    ("ADB_PATH",       "adb",       "Android device bridge (ADB)"),
    ("FFMPEG_PATH",    "ffmpeg",    "video / audio processing (FFmpeg)"),
    ("MAGICK_PATH",    "magick",    "image processing (ImageMagick)"),
    ("PANDOC_PATH",    "pandoc",    "document conversion (Pandoc)"),
    ("SOFFICE_PATH",   "soffice",   "office documents (LibreOffice)"),
    ("PYTHON_PATH",    "python",    "Python interpreter (custom path)"),
]

# Whole-word task cues that mean "this turn is about producing a document / media
# / device artifact" even when no tool is named explicitly.
_HOST_TASK_WORDS = (
    "document", "word", "excel", "spreadsheet", "pdf", "slides", "presentation",
    "docx", "xlsx", "pptx", "3d", "render", "modelling", "modeling",
    "video", "audio", "image", "convert", "android", "office", "libreoffice",
)

# Effect tools whose use means a host-tool run command may be worth stating.
_HOST_EFFECT_TOOLS = frozenset({"shell_exec", "file_write"})

_HOST_RELEVANCE_RE = re.compile(
    r"\b(" + "|".join(
        re.escape(name) for _env, name, _desc in _HOST_ENV_TOOLS
    ) + "|" + "|".join(re.escape(w) for w in _HOST_TASK_WORDS) + r")\b",
    re.IGNORECASE,
)


def host_tools_relevant(text: str, tools_used: "Iterable[str]" = ()) -> bool:
    """Deterministic gate: is this turn actually about host tools?

    True when the text (question + planner reasoning) mentions a host-tool name
    or a document/media/device task word (whole-word, so "word" inside "keyword"
    does NOT fire), OR the plan actually used an effect tool (``shell_exec`` /
    ``file_write``). No LLM. Used to avoid injecting ``.env`` host paths into
    every synthesizer prompt (LPF-001 iteration 1b).
    """
    if _HOST_RELEVANCE_RE.search(text or ""):
        return True
    used = {str(t).strip().lower() for t in (tools_used or ()) if t}
    return bool(used & _HOST_EFFECT_TOOLS)


def _build_host_tools_block() -> str:
    """Read tool paths from env vars (loaded from .env) and return a planner
    context block listing what is actually installed on this host.
    Only includes vars that are set and non-empty.
    """
    _ENV_TOOLS = _HOST_ENV_TOOLS
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
    [read_only for whoami/hostname/where/which/git-reads/findstr/grep;
     irreversible for mkdir/touch and for git add/commit/checkout —
     those escalate to human approval and ship with a compensation plan]
    Runs ONE whitelisted command inside the workspace sandbox.
    Whitelist (the ONLY allowed argv[0] values):
      read-only : whoami, hostname, where, which,
                  git (subcommand restricted to: log, diff, status, show,
                       branch, tag, blame, rev-parse, describe, ls-files,
                       ls-tree, cat-file, shortlog, reflog, name-rev),
                  findstr (Windows) / grep (POSIX) — used as a content
                  search across many files in one call
      mutating  : mkdir, touch  (exactly one path argument, inside workspace)
      recording : git add / git commit / git checkout — you CAN record your
                  own work. Each has ONE allowed shape:
                    ["git","checkout","-b","agent/<name>"]  create your own
                        branch (you may NOT switch to an existing one)
                    ["git","add","<path>", ...]             explicit paths
                        only — never "-A"
                    ["git","commit","-m","<message>"]       nothing else —
                        no --amend, no --no-verify
                  Committing is REFUSED on main/master, so create the
                  agent/… branch FIRST, then add, then commit.
                  Still forbidden: push, pull, fetch, clone, reset, rebase,
                  merge, stash. Do not report "I cannot commit" without
                  having tried these shapes.
    For "find/count X across N files" sweeps, prefer ONE
    `findstr`/`grep` call over N file_read calls — cheaper, faster,
    no truncation per-file.
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

11a. DOCTRINE / CORPORATE MODEL QUESTIONS — user asks about the declared
    corporate model, central agent governance, subagents, self-build,
    night observation, safe autonomy, or the intended multi-agent doctrine.

    MANDATORY docs-first plan:
      -> [file_read docs/future/CORPORATE_MODEL.md,
          file_read docs/CENTRAL_AGENT_GOVERNANCE.md,
          file_read docs/AGENT_ANATOMY.md,
          file_read docs/ROADMAP.md,
          file_read docs/COMMANDS_MAP.md]

    SUB-AGENT SUB-TOPIC — if (and only if) the question is specifically about
    sub-agents, delegation, the team executor, role trust, quarantine, pausing,
    retiring, or the sub-agent lifecycle, ALSO read docs/SUBAGENT_LIFECYCLE.md
    (the normative sub-agent lifecycle contract). Do NOT read it for unrelated
    corporate-model / roadmap / governance questions.

    Do NOT start with README.md or central mechanics code such as
    core/planner.py, core/loop.py, core/autonomous_runtime.py,
    core/self_repair.py, or core/smart_memory.py unless the user explicitly
    asks whether a behavior is implemented in code or asks for a critique of
    real implementation gaps. Doctrine questions need the doctrine docs first.

11b. IMPLEMENTATION CHECK — user asks whether a SPECIFIC FEATURE or BEHAVIOR
    is already implemented / done / working in THIS agent's codebase.
    Russian trigger phrases: "это уже сделано?", "реализовано ли", "уже есть?",
    "это уже работает?", "есть ли в коде", "проверь код".
    English trigger phrases: "is X implemented", "is X done", "does X work",
    "has X been added", "is there code for X".

    CRITICAL: README.md describes the INTENDED architecture — it is NOT
    ground truth about what is actually coded. The source files ARE.
    -> DO NOT use [file_read README.md] for these questions.
    -> Instead, identify the most likely source module and read it.

    Source file heuristics for common topics:
      learning / staleness / autonomous learning   -> core/learning_planner.py
      episodic memory / eviction / lesson search   -> core/smart_memory.py
      repair / self-repair / repair lessons        -> core/self_repair.py
      dry_run / CLI / work-session / :work-session -> main.py
      budget / cost / rate limits                  -> core/budget_governor.py
      planner / planning / tool selection          -> core/planner.py
      loop / agent run / conversation turn         -> core/loop.py
      scheduling / autonomous runtime / cron       -> core/autonomous_runtime.py

    If uncertain which file applies, use [list_dir core/] first to see
    what modules exist, then read the 1-2 most plausible ones.

    EXAMPLE:
      "Хочу чтобы агент не читал недавно прочитанные файлы при обучении. Уже сделано?"
      -> [file_read core/learning_planner.py]
      (NOT [file_read README.md] — README won't tell you if _apply_staleness exists)

11c. ARCHITECTURE CRITIQUE / SELF-CRITIQUE — user asks what is WRONG with the
    architecture, what should be CHANGED, what are the REAL gaps, how would
    YOU redesign it, what is inconsistent, what is broken.
    Russian trigger phrases: "что не так с архитектурой", "как бы ты изменил",
    "что бы ты переделал", "найди настоящие проблемы", "что реально сломано",
    "в чём слабость", "критикуй архитектуру", "что улучшить", "что плохо спроектировано".
    English trigger phrases: "what's wrong with the architecture", "how would you redesign",
    "what are the real gaps", "critique the design", "what would you change".

    CRITICAL DISTINCTION from rule 11 (introspective):
    Rule 11 = "describe yourself" → README is OK (user wants the declared architecture).
    Rule 11c = "critique yourself" → README is NOT enough (it describes INTENT, not REALITY).
    A good self-critique MUST check the actual code, not just the README's TODO list.

    MANDATORY plan for rule 11c:
    Step 1: [list_dir core/] — see all modules
    Step 2-4: read 3-4 key source files that implement the most complex interactions.
    Prioritise modules where bugs were likely found before:
      core/loop.py               (the main cognitive loop — where do episodes go?)
      core/self_repair.py        (repair lessons — are they written to the right store?)
      core/autonomous_runtime.py (autonomous task execution — does goal task really run?)
      core/learning_planner.py   (learning — does it avoid re-reading recent files?)
      core/smart_memory.py       (episodic store — is eviction + protection implemented?)

    DO NOT stop at README. A meaningful critique requires seeing the actual code.
    README only tells you the declared design. The gap between declaration and
    implementation IS the architecture critique.

    EXAMPLE:
      "Что не так с архитектурой и как бы ты её изменил?"
      -> [list_dir core/, file_read core/loop.py,
          file_read core/self_repair.py, file_read core/autonomous_runtime.py]
      Then synthesize: where does the code diverge from intent? What is missing
      that no TODO in README mentions? That is the real critique.

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

# §3.x — register this prompt with the global Prompt Registry
try:
    from core.prompt_registry import register_prompt as _rp
    _rp("planner.system", PLANNER_SYSTEM, module="core.planner",
        description="Planner system prompt: tool catalog + decision rules (§3 Cognitive Core)")
except ImportError:  # pragma: no cover
    pass


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
    # Structured, human-readable JSON-parse diagnostics (TD-003). Populated by
    # ``_parse_json`` so operators can see *why* an output failed to parse
    # (brief reason, which stage broke, whether a JSON block was found, which
    # fallback was chosen) plus a sanitised, length-limited preview of the raw
    # output — without another LLM call and without leaking full secrets.
    diagnostics: dict[str, Any] = field(default_factory=dict)


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
        # Run-scoped set of tool names to hide from the planner surface. Empty
        # by default (REPL sees every registered tool). AutonomousRuntime._task_goal
        # sets this to the run-scoped block set so the planner never *proposes*
        # tools that PolicyGate would deny on the unattended goal path. Policy
        # remains the defense-in-depth block at execution time.
        self.hidden_tools: frozenset[str] = frozenset()
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
            max_tokens=_plan_max_tokens(),
            temperature=0.0,
        )
        parsed, parse_warnings, parse_diag = self._parse_json(raw)
        if parsed is None:
            return PlannerOutput(
                reasoning="(planner output did not parse — falling back to empty plan)",
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["plan_parse_failed"],
                diagnostics=parse_diag,
            )

        reasoning = str(parsed.get("reasoning", "")).strip() or "(no reasoning provided)"
        raw_steps = parsed.get("steps") or []
        if not isinstance(raw_steps, list):
            parse_diag = {**parse_diag, "reason": "steps field was not a list"}
            return PlannerOutput(
                reasoning=reasoning,
                sources=[],
                raw_response=raw,
                warnings=parse_warnings + ["steps_field_not_a_list"],
                diagnostics=parse_diag,
            )

        sources, step_warnings, dropped_tools = self._validate_steps(
            raw_steps, file_hint, forbidden_actions
        )
        if _should_prefer_memory_over_readme(question, history):
            sources = _drop_readme_status_sources(sources, step_warnings)
        if _is_self_repo_introspection_question(question):
            sources = _drop_web_lookup_for_introspection(sources, step_warnings)
        if _is_confidence_evidence_diagnostic_question(question):
            if "file_read" in self.hidden_tools:
                step_warnings.append(
                    "confidence/evidence verifier sources required but file_read is hidden on this path"
                )
            else:
                try:
                    self.registry.get("file_read")
                except KeyError:
                    step_warnings.append(
                        "confidence/evidence verifier sources required but file_read is not registered"
                    )
                else:
                    sources = _ensure_confidence_evidence_sources_first(
                        sources,
                        step_warnings,
                        drop_low_signal_defaults=(
                            not _explicitly_requests_readme(question)
                        ),
                    )
        if _is_doctrine_corporate_question(question):
            if "file_read" in self.hidden_tools:
                step_warnings.append(
                    "doctrine/corporate docs required but file_read is hidden on this path"
                )
            else:
                try:
                    self.registry.get("file_read")
                except KeyError:
                    step_warnings.append(
                        "doctrine/corporate docs required but file_read is not registered"
                    )
                else:
                    sources = _ensure_doctrine_docs_first(
                        sources,
                        step_warnings,
                        drop_default_code_sources=(
                            not _requests_implementation_detail(question)
                            and not _explicitly_requests_readme(question)
                        ),
                    )
        if _is_subagent_governance_question(question) and self._file_read_available():
            sources = _ensure_subagent_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_memory_governance_question(question) and self._file_read_available():
            sources = _ensure_memory_governance_docs_first(
                sources,
                step_warnings,
            )
        if _is_self_repair_doctrine_question(question) and self._file_read_available():
            sources = _ensure_self_repair_doctrine_docs_first(
                sources,
                step_warnings,
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
            diagnostics=parse_diag,
        )

    # ---------- prompt construction ----------

    def _file_read_available(self) -> bool:
        """Whether a `file_read` step can actually run on this path.

        Doc injection and the matching prompt directive must agree. Injection
        was already gated on this; the directives were not, so on the
        autonomous path (where `file_read` is hidden) the prompt still ordered
        the model to "read docs/X first" while the registered-tools list and
        the [UNAVAILABLE_TOOLS=...] block said it could not. That contradiction
        is exactly the noisy policy_blocked replan the hidden-tools directive
        exists to prevent, so the gate lives here once and both sides use it.
        """
        if "file_read" in (getattr(self, "hidden_tools", frozenset()) or frozenset()):
            return False
        try:
            self.registry.get("file_read")
        except KeyError:
            return False
        return True

    def _build_user_prompt(
        self,
        question: str,
        file_hint: str | None,
        history: str = "",
        failure_context: str = "",
    ) -> str:
        today = datetime.now(timezone.utc).strftime("%Y-%m-%d")
        hint = file_hint if file_hint else "(none)"
        hidden = getattr(self, "hidden_tools", frozenset()) or frozenset()
        tool_names = ", ".join(
            t.name for t in self.registry.list() if t.name not in hidden
        )
        # When tools are hidden for this path (autonomous goal path), do not
        # merely omit them — the static system prompt still describes tools like
        # spawn_subagent. Add an explicit directive so the planner never selects
        # a run-scoped-blocked tool (avoids noisy policy_blocked replans).
        if hidden:
            unavailable_block = (
                f"[UNAVAILABLE_TOOLS={', '.join(sorted(hidden))} — these tools are "
                "NOT available on this path and MUST NOT appear in your plan. "
                "Ignore any general guidance that suggests them; use only the "
                "registered tools listed above.]\n"
            )
        else:
            unavailable_block = ""

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
        project_memory_block = ""
        if _should_prefer_memory_over_readme(question, history):
            project_memory_block = (
                "[PROJECT_STATUS_MEMORY=preferred — long_term_memory already "
                "contains recent project/status records. Do NOT plan "
                "file_read README.md for live project status. README.md may "
                "only be used when the user explicitly asks for README or "
                "architecture/reference facts.]\n"
            )
        doctrine_docs_block = ""
        # Every "read docs/X first" directive is gated on file_read actually
        # being usable — an unreachable instruction only produces plans the
        # policy layer then blocks.
        docs_readable = self._file_read_available()
        if docs_readable and _is_doctrine_corporate_question(question):
            doctrine_docs_block = (
                "[DOCTRINE_DOCS=required — for corporate model, central agent "
                "governance, subagents, self-build, night observation, and "
                "safe autonomy questions, start with docs/future/CORPORATE_MODEL.md, "
                "docs/CENTRAL_AGENT_GOVERNANCE.md, docs/AGENT_ANATOMY.md, "
                "docs/ROADMAP.md, and docs/COMMANDS_MAP.md before central "
                "core/*.py mechanics.]\n"
            )
        subagent_docs_block = ""
        if docs_readable and _is_subagent_governance_question(question):
            subagent_docs_block = (
                "[SUBAGENT_DOCS=required — this question is about sub-agents / "
                "delegation / team executor / role trust / quarantine / pause / "
                "retire / lifecycle. Read docs/SUBAGENT_LIFECYCLE.md first (the "
                "normative sub-agent lifecycle contract) before core/*.py "
                "mechanics.]\n"
            )
        memory_docs_block = ""
        if docs_readable and _is_memory_governance_question(question):
            memory_docs_block = (
                "[MEMORY_DOCS=required — this question is about memory / "
                "episodic / procedural / consolidation / forgetting / retrieval "
                "/ durable learning. Read docs/audit/MEMORY_MAP.md (how memory "
                "actually flows today), docs/MEMORY_SYSTEM_AUDIT.md and "
                "docs/self-audit-lessons.md first, before core/*.py mechanics. "
                "These record known defects and their causes — do not "
                "re-derive them from the code.]\n"
            )
        self_repair_docs_block = ""
        if docs_readable and _is_self_repair_doctrine_question(question):
            self_repair_docs_block = (
                "[SELF_REPAIR_DOCS=required — this question is about "
                "self-diagnosis / self-repair / root cause / regression / "
                "backfill / data migration. Read docs/SELF_REPAIR_DOCTRINE.md "
                "first (the normative repair protocol: prove the defect, "
                "separate symptom from cause, never reconstruct data by guess, "
                "fail closed, migrate safely, bank the lesson only after the "
                "verdict closes) before core/*.py mechanics.]\n"
            )
        confidence_evidence_block = ""
        if _is_confidence_evidence_diagnostic_question(question):
            confidence_evidence_block = (
                "[CONFIDENCE_EVIDENCE_DIAGNOSTIC_SOURCES=required — for questions "
                "about evidence support, evidence, citations, "
                "verifier, verified/unverified chunks, or source registry, start "
                "with core/verifier.py, tests/test_verifier.py, "
                "tests/test_evidence_support.py, and "
                "tests/test_confidence_vector.py. Do NOT use README.md or "
                "list_dir tools/ as primary evidence for confidence-gate internals.]\n"
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
            f"{unavailable_block}"
            f"\n"
            f"{history_block}"
            f"{replan_block}"
            f"{grounding_block}"
            f"{project_memory_block}"
            f"{confidence_evidence_block}"
            f"{doctrine_docs_block}"
            f"{subagent_docs_block}"
            f"{memory_docs_block}"
            f"{self_repair_docs_block}"
            f"question: {question}\n"
            f"\n"
            f"Return your JSON plan now."
        )

    # ---------- JSON parsing ----------

    # Max characters of raw planner output echoed into diagnostics. Keeps trace
    # logs bounded and, combined with DLP redaction, avoids leaking full secrets.
    _RAW_PREVIEW_LIMIT = 200

    @staticmethod
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

    @staticmethod
    def _parse_json(
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
            "raw_preview": LLMPlanner._sanitized_preview(raw),
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

            spec = sanitize_step(
                tool_name, args, file_hint, idx, warnings,
                self_documentation_paths=self.self_documentation_paths,
            )
            if spec is None:
                continue
            sources.append(spec)

        return sources, warnings, dropped_tools
