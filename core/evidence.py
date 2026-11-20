"""Evidence + provenance model: every claim in an answer is tied to a typed Evidence record; one tool output gives at most one evidence, and a tool error means no source, not a weak one."""
from __future__ import annotations

import hashlib
import json
import os
import posixpath
import re
from dataclasses import dataclass, field, replace
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.ids import new_id
from core.injection_guard import carries_suspicious_annotation


EvidenceKind = Literal[
    "file",             # file_read result — workspace file contents
    "web_page",         # web_fetch result — actual fetched page body
    "web_search_hit",   # web_search result — pointer, NOT a source
    "tool_output",      # fallback for generic / unknown-shape tool output
    "test_result",      # run_tests — pytest pass/fail counts
    "log_event",        # read_logs — JSONL events from the audit log
    "shell_output",     # shell_exec — captured stdout/stderr
    "diff_preview",     # diff_file — unified diff, not yet applied
    "memory",           # memory record retrieved from working / persistent store
    "session_dialogue", # verbatim earlier turn of THIS session (issue #119)
    "user_explicit",    # :remember / explicit consent / direct user command
    "runtime",          # this process measuring ITSELF — interpreter, pid, cwd
    "sensor",           # a block the loop read from ITS OWN journals (model roster, spend mirror)
    "llm_claim",        # LLM-generated text WITHOUT external grounding
    "unknown",          # last-resort bucket
]

ALL_EVIDENCE_KINDS: tuple[EvidenceKind, ...] = (
    "file", "web_page", "web_search_hit", "tool_output", "test_result",
    "log_event", "shell_output", "diff_preview", "memory",
    "session_dialogue", "user_explicit", "runtime", "sensor", "llm_claim", "unknown",
)


# Baseline confidence per source kind, "all else equal"; the Verifier applies modifiers on top.
# Order = source hierarchy: user-explicit > tested code > workspace file > log > shell > diff preview > fetched page > generic tool output > memory > web-search pointer > llm-claim > unknown.
DEFAULT_CONFIDENCE: dict[EvidenceKind, float] = {
    "user_explicit":    1.00,
    "sensor":           0.90,  # measured about this agent by its own sensors
    # The process reading its own interpreter, pid and cwd ranks above a workspace file (a file can be stale at once) but its scope is this run only; core/evidence_classes.py keeps it in the `trace` class so it never confirms a claim about the world.
    "runtime":          0.95,
    "test_result":      0.95,
    "file":             0.90,
    "log_event":        0.90,
    "shell_output":     0.85,
    "diff_preview":     0.80,
    "web_page":         0.75,
    "tool_output":      0.70,
    # A recording of what was said in this session: high fidelity but scope is one dialogue turn, so it never confirms a world claim (core/evidence_classes.py). The number ranks retrieval, not authority.
    "session_dialogue": 0.60,
    "memory":           0.55,
    "web_search_hit":   0.35,
    "llm_claim":        0.20,
    "unknown":          0.10,
}

# Maximum excerpt length stored on disk / sent into prompts.
MAX_EXCERPT_CHARS = 800

#: Boundary between the experiment's outcome and its QUESTION (the code). Everything after the marker is not truth evidence: names the turn itself wrote live there.
QUESTION_CODE_MARKER = "[QUESTION-CODE: the experiment's own code, not evidence of its answer]"

#: Service fields this module wraps around a tool outcome (exit code, duration, truncation flag). They describe the run, not the world, and are data for no judge: the arithmetic gate once summed `exit_code: 0` plus `duration_ms: 9` into a false "sum". Each collector has its own envelope; all names are listed here beside the strings that print them.
ENVELOPE_KEYS: frozenset[str] = frozenset({
    "exit_code", "timed_out", "duration_ms", "stdout_truncated",
    "inputs", "missing_inputs", "stdout", "stderr", "returncode",
    "bytes", "lines", "latency_ms", "cost", "status",
})



@dataclass(frozen=True)
class Evidence:
    """One immutable piece of supporting evidence for a claim."""
    id: str
    kind: EvidenceKind
    source_id: str           # path / URL / trace_id#event / mem_xxx / req_xxx
    obtained_via: str        # tool name, "memory", or "user_explicit"
    content_hash: str        # sha256 of excerpt
    fetched_at: str          # ISO-8601 UTC
    confidence: float        # in [0, 1]
    claim: str               # short human-readable claim this evidence supports
    excerpt: str             # first MAX_EXCERPT_CHARS chars of the source
    # For memory: the record's `source` ("user-explicit", "agent-auto", ...); else None.
    origin: str | None = None
    # Full source text read: kept only in the turn's memory, for claim cross-checks.
    source_text: str = field(default="", compare=False, repr=False)

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "kind": self.kind,
            "source_id": self.source_id,
            "obtained_via": self.obtained_via,
            "content_hash": self.content_hash,
            "fetched_at": self.fetched_at,
            "confidence": self.confidence,
            "claim": self.claim,
            "excerpt": self.excerpt,
            "origin": self.origin,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Evidence:
        return cls(
            id=data["id"],
            kind=data["kind"],
            source_id=data["source_id"],
            obtained_via=data["obtained_via"],
            content_hash=data["content_hash"],
            fetched_at=data["fetched_at"],
            confidence=float(data["confidence"]),
            claim=data["claim"],
            excerpt=data["excerpt"],
            origin=data.get("origin"),
        )


@dataclass
class ProvenanceChain:
    """Per-cycle ordered collection of Evidence."""
    evidences: list[Evidence] = field(default_factory=list)

    def add(self, ev: Evidence) -> None:
        self.evidences.append(ev)

    def extend(self, items: list[Evidence]) -> None:
        self.evidences.extend(items)

    def __len__(self) -> int:
        return len(self.evidences)

    def by_source_id(self, source_id: str) -> Evidence | None:
        for ev in self.evidences:
            if ev.source_id == source_id:
                return ev
        return None

    def by_kind(self, kind: EvidenceKind) -> list[Evidence]:
        return [ev for ev in self.evidences if ev.kind == kind]

    def highest_confidence(self) -> Evidence | None:
        if not self.evidences:
            return None
        return max(self.evidences, key=lambda e: e.confidence)

    def to_log_payload(self) -> list[dict[str, Any]]:
        """Compact log shape — drops `excerpt` to keep JSONL entries small.

        Audit consumers that need the full text can replay from the
        `tool_result` events that produced these evidences.
        """
        out: list[dict[str, Any]] = []
        for ev in self.evidences:
            d = ev.to_dict()
            d["excerpt_len"] = len(d.pop("excerpt", "") or "")
            out.append(d)
        return out



def compute_content_hash(text: str) -> str:
    """sha256 hex of the utf-8 bytes. errors='replace' so a non-UTF-8
    blob still gets a stable hash instead of crashing the loop."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


#: A search's honest report of NOT FOUND (`tools/find_in_files.py:128`): it must name the thing sought.
_NO_MATCHES_RE = re.compile(r"^no matches for .+? in (?P<tail>\d+ text files.*)$",
                            re.IGNORECASE | re.DOTALL)


def _search_excerpt(output: str) -> str:
    """Search outcome kept apart from its query.

    An empty search result repeats the query by construction; the echo goes behind a marker that truth gates cut off (`core.verifier_utils.truth_excerpt`), while the literal gate still sees the name is not invented. Hits are left untouched.
    """
    match = _NO_MATCHES_RE.match(output.strip())
    if not match:
        return output
    return (f"no matches in {match.group('tail')}\n"
            f"{QUESTION_CODE_MARKER}\n{output.strip()}")


def _page_body(text: str) -> str:
    """Page text without the `[find ...]` header (the header holds query words, not page words)."""
    head, sep, rest = text.partition("\n")
    return rest if sep and head.startswith("[find ") and head.endswith("]") else text


def _truncate(text: str, limit: int = MAX_EXCERPT_CHARS) -> str:
    if not isinstance(text, str):
        text = str(text)
    if len(text) <= limit:
        return text
    return text[:limit] + "...[truncated]"


def make_evidence(
    *,
    kind: EvidenceKind,
    source_id: str,
    obtained_via: str,
    claim: str,
    excerpt: str,
    confidence: float | None = None,
    fetched_at: str | None = None,
    origin: str | None = None,
    source_text: str = "",
) -> Evidence:
    """Constructor that fills in id / hash / timestamp / baseline confidence."""
    if kind not in DEFAULT_CONFIDENCE:
        raise ValueError(f"unknown evidence kind: {kind!r}")
    excerpt_trimmed = _truncate(excerpt)
    conf = float(confidence if confidence is not None else DEFAULT_CONFIDENCE[kind])
    if conf < 0.0 or conf > 1.0:
        raise ValueError(f"confidence must be in [0, 1], got {conf}")
    return Evidence(
        id=new_id("ev"),
        kind=kind,
        source_id=source_id,
        obtained_via=obtained_via,
        content_hash=compute_content_hash(excerpt_trimmed),
        fetched_at=fetched_at or _now_utc_iso(),
        confidence=conf,
        claim=claim,
        excerpt=excerpt_trimmed,
        origin=origin,
        source_text=source_text,
    )



#: Ceiling of the experiment outcome excerpt. Measured on 478 python_probe analyses: at 520 chars 77% of outcomes fit whole, at 2000 97%, beyond that each percent cost triple. Cost: +118 chars per evidence on average (median outcome 316).
_PROBE_OUTCOME_CHARS = 2000

#: How many chars of outcome go to the output itself, leaving room for the header.
_PROBE_STDOUT_BUDGET = 1500


def _condense_probe_stdout(stdout: str, budget: int = _PROBE_STDOUT_BUDGET) -> str:
    """Probe stdout within `budget`, KEEPING lines with numbers.

    Claim checks need every named number verbatim in the excerpt (`core.verifier_utils._excerpt_supports_figures`); head truncation lost numbers. Lines with digits are taken first in original order, the rest of the space goes to the others. Omission is announced aloud, never silent.
    """
    text = stdout or ""
    if len(text) <= budget:
        return text
    lines = text.split("\n")
    with_digits = [i for i, ln in enumerate(lines) if any(c.isdigit() for c in ln)]
    keep: set[int] = set()
    used = 0
    for i in with_digits:
        cost = len(lines[i]) + 1
        if used + cost > budget:
            break
        keep.add(i)
        used += cost
    for i, ln in enumerate(lines):
        if i in keep:
            continue
        cost = len(ln) + 1
        if used + cost > budget:
            continue
        keep.add(i)
        used += cost
    dropped_numeric = sum(1 for i in with_digits if i not in keep)
    dropped_plain = len(lines) - len(keep) - dropped_numeric
    kept = "\n".join(lines[i] for i in sorted(keep))
    note = f"...[опущено строк: {dropped_numeric} с числами, {dropped_plain} без]"
    return kept + "\n" + note


def _python_probe_evidence(args: dict[str, Any], output: Any) -> Evidence | None:
    """Experiment evidence is its OUTCOME; the code is the question and stays out of the excerpt (see docs/CODE_NOTES.md).

    The outcome fields (missing_inputs, truncation) must be in the excerpt or the literal gate reads them as invented.
    """
    if not isinstance(output, dict):
        return None
    code = str(args.get("code") or output.get("code") or "").strip()
    head = code.splitlines()
    outcome = (
        f"exit_code: {output.get('exit_code')}\n"
        f"timed_out: {output.get('timed_out', False)}\n"
        f"inputs: {json.dumps(output.get('inputs') or [], ensure_ascii=False)}\n"
        f"missing_inputs: {json.dumps(output.get('missing_inputs') or [], ensure_ascii=False)}\n"
        f"stdout_truncated: {output.get('stdout_truncated', False)}\n"
        f"duration_ms: {output.get('duration_ms')}\n"
        f"stdout:\n{_condense_probe_stdout(str(output.get('stdout') or ''))}\n"
        f"stderr:\n{output.get('stderr') or ''}"
    )
    # Names come from the question, truth from the outcome: the code is attached as a SEPARATE part behind a marker; the literal gate sees names exist, truth gates cut that part (`core.verifier_utils.truth_excerpt`) so the question never confirms its own answer.
    excerpt = _truncate(outcome, _PROBE_OUTCOME_CHARS)
    if code:
        excerpt += f"\n{QUESTION_CODE_MARKER}\n" + _truncate(code, 240)
    return make_evidence(
        kind="tool_output",
        source_id="tool_output:python_probe",
        obtained_via="python_probe",
        claim=f"Measured outcome of a live experiment: {head[0][:80] if head else '?'}",
        excerpt=excerpt,
    )


#: Body of a stub page: the server answered 200 but the content is an error, login form, captcha or domain-for-sale notice (historical class H-01: a diagnostic mistaken for data).
_ERROR_PAGE_MARKERS: tuple[str, ...] = (
    "404 not found", "not found", "403 forbidden", "forbidden",
    "service unavailable", "internal server error", "bad gateway",
    "access denied", "sign in to continue", "you must be logged in",
    "log in to continue", "verify you are human", "enable javascript",
    "checking your browser", "this domain is parked", "domain for sale",
    "rate limit", "too many requests",
    "страница не найдена", "не найдена", "доступ запрещ",
    "войдите в систему", "включите javascript", "слишком много запросов",
)

#: The shape of a stub, not its length (a length threshold was fitting: a real article on soft-404 was 581 chars). What differs is the share the marker takes in the body; hence two conditions at once: the body is short AND made of counted sentences.
_ERROR_PAGE_MAX_CHARS = 600
_ERROR_PAGE_MAX_SENTENCES = 3
_SENTENCE_BREAK = re.compile(r"[.!?" + chr(92) + "n]+")


#: Browser check instead of a page (anti-bot): such a marker is the whole body by itself, though a line-break sentence counter would let it pass.
_INTERSTITIAL_MARKERS: tuple[str, ...] = (
    "client challenge", "a required part of this site couldn", "checking your browser",
    "verify you are human", "just a moment...", "attention required!",
)


def looks_like_error_page(text: str) -> bool:
    """Whether the page body is a service stub, not content."""
    body = (text or "").strip()
    if not body or len(body) > _ERROR_PAGE_MAX_CHARS:
        return False
    if any(marker in body.lower().replace("’", "'") for marker in _INTERSTITIAL_MARKERS):
        return True
    sentences = [p for p in re.split(_SENTENCE_BREAK, body) if p.strip()]
    if len(sentences) > _ERROR_PAGE_MAX_SENTENCES:
        return False
    lowered = body.lower()
    return any(marker in lowered for marker in _ERROR_PAGE_MARKERS)


#: Memory whose provenance makes it independent evidence: only a human's own assertion; the agent's auto-written record is a copy of what it already said.
_INDEPENDENT_MEMORY_ORIGINS: frozenset[str] = frozenset({"user-explicit"})


def evidence_is_independent(ev: Any) -> bool:
    """May this evidence count as INDEPENDENT support (`verified`)? The one rule.

    - the agent's own state/journals/drafts (`origin="agent-own"`): never;
    - memory recall (`memory_recall`) and persistent memory records (`obtained_via="memory"`): only when the origin is a human's;
    - a cached real tool observation and every other kind: yes.
    Every verifier road must ask THIS function; a per-road rule was bypassed.
    """
    if getattr(ev, "origin", None) == "agent-own":
        return False
    via = getattr(ev, "obtained_via", None)
    if via == "memory_recall":
        return getattr(ev, "origin", None) in _INDEPENDENT_MEMORY_ORIGINS
    if getattr(ev, "kind", None) == "memory" and via == "memory":
        return getattr(ev, "origin", None) in _INDEPENDENT_MEMORY_ORIGINS
    return True


def _memory_recall_evidence(args: dict[str, Any], output: Any) -> Evidence | None:
    """The agent reading its own memory: independent only if EVERY record is a human's.

    Kind stays `tool_output` so the `[tool:memory_recall]` citation resolves; `_memory_citation_is_independent` judges the origin.
    """
    if not isinstance(output, dict):
        return None
    records = [r for r in (output.get("records") or []) if isinstance(r, dict)]
    if not records:
        return None
    human = all(r.get("source") == "user-explicit" for r in records)
    return make_evidence(
        kind="tool_output",
        source_id="tool_output:memory_recall",
        obtained_via="memory_recall",
        claim=f"Own memory recalled for {args.get('term', '')!r} (low trust)",
        excerpt="\n".join(str(r.get("content", "")) for r in records),
        confidence=DEFAULT_CONFIDENCE["memory"],
        origin="user-explicit" if human else "agent-auto",
    )


#: Workspace folders holding what the agent wrote ITSELF (state, journals, drafts, generated docs). By folder only, relative to the workspace: a user's `build/server.log` elsewhere stays ordinary evidence.
_OWN_STATE_DIRS = ("data/", "logs/", "proposals/", "knowledge/generated/")
_REPO_ROOT = Path(__file__).resolve().parents[1]
#: Other installs of THIS repo (Ubuntu, unpacked archive); a foreign `.../agent/data/` is not ours.
_INSTALL_ROOTS = ("/opt/agent",)
_ARCHIVE_DIR = "/agent-main/"
_ABSOLUTE_RE = re.compile(r"^(/|[a-z]:/)")


def _norm_path(path: Any) -> str:
    """Slashes, `.` and `..` resolved, case folded: `DOCS\..\Data` -> `data`."""
    text = str(path or "").strip().strip("'\"`").replace("\\", "/")
    return posixpath.normpath(text).casefold() if text else ""


def _agent_roots() -> list[str]:
    roots = [_norm_path(_REPO_ROOT.as_posix()), *_INSTALL_ROOTS]
    workspace = os.environ.get("AGENT_WORKSPACE")
    if workspace:
        roots.append(_norm_path(Path(workspace).resolve().as_posix()))
    return roots


def _path_inside_agent(path: Any) -> str | None:
    """The path relative to the agent's root (relative = workspace), or None outside it."""
    norm = _norm_path(path)
    if not norm or norm == ".." or norm.startswith("../"):
        return None
    if not _ABSOLUTE_RE.match(norm):
        return norm
    for root in _agent_roots():
        base = root.rstrip("/")
        if norm == base:
            return "."
        if norm.startswith(base + "/"):
            return norm[len(base) + 1:]
    if _ARCHIVE_DIR in norm:
        return norm.split(_ARCHIVE_DIR, 1)[1]
    return None


def _is_own_state_path(path: Any) -> bool:
    """Does the path point into the agent's own state folders (data/, logs/, …)?"""
    rel = _path_inside_agent(path)
    return bool(rel) and any(rel == d.rstrip("/") or rel.startswith(d) for d in _OWN_STATE_DIRS)


_SHELL_TOKEN_RE = re.compile(r"[\s;|&<>()=,]+")
#: A word the shell expands (glob, variable, substitution) or the whole workspace may reach own state without naming it: `cat d*/x`, `grep -r foo .`.
_SHELL_OPAQUE_RE = re.compile(r"[*?\[{$`]")
_SHELL_WHOLE_TREE = frozenset({".", "./", "*"})


def _shell_word_may_be_own(tok: str) -> bool:
    word = tok.strip("'\"")
    return word in _SHELL_WHOLE_TREE or bool(_SHELL_OPAQUE_RE.search(word)) or _is_own_state_path(word)


def _shell_touches_own_state(*commands: Any) -> bool:
    """Any word of the command line that names or may expand into own state (conservative)."""
    for command in commands:
        parts = command if isinstance(command, (list, tuple)) else [command]
        for part in parts:
            if any(_shell_word_may_be_own(tok) for tok in _SHELL_TOKEN_RE.split(str(part or "")) if tok):
                return True
    return False


_HIT_PATH_RE = re.compile(r"^(?P<path>.+?):\d+:")


def _search_touches_own_state(path: Any, output: str) -> bool:
    """A search inside own state, or one whose hits/files come from own state."""
    if _is_own_state_path(path):
        return True
    for line in output.splitlines():
        match = _HIT_PATH_RE.match(line)
        if _is_own_state_path(match.group("path") if match else line):
            return True
    return False


def _file_read_evidence(args: dict[str, Any], output: Any) -> Evidence | None:
    """Evidence for a `file_read` result, whole file or a line window.

    A windowed read is its own source, named as the plan step labels it (`file:<path>:a-b`, core/step_sanitizer.py), or correct citations of the label are judged fabricated.
    """
    if not isinstance(output, str) or not output:
        return None
    path = str(args.get("path", "<unknown>"))
    window = ""
    start, end = args.get("start_line"), args.get("end_line")
    if isinstance(start, int) and isinstance(end, int):
        window = f":{start}-{end}"
    from core.redaction import redact_text

    # One masking for the excerpt and the full text: the cross-check sees no more than the excerpt.
    output = redact_text(output)[0]
    # The agent's own state, journals and drafts are a record of itself, not the world. Kind stays `file` so `[file:data/...]` citations resolve; origin `agent-own` marks it as not INDEPENDENT support.
    own = _is_own_state_path(path)
    return make_evidence(
        kind="file",
        origin="agent-own" if own else None,
        source_id=f"file:{path}{window}",
        obtained_via="file_read",
        claim=f"Contents of workspace file {path}"
        + (f", lines {start}-{end}" if window else ""),
        excerpt=output,
        source_text=output,
    )


def _convert_file_evidence(args: dict[str, Any], output: Any) -> Evidence | None:
    """Evidence from convert_file: recognised text, else the path of the result file."""
    if not isinstance(output, dict):
        return None
    # source_id = step label = "file:<source file>" (tools/convert_file.sanitize_args), as for file_read; the verifier does not know a "convert_file" citation kind (CITATION_PREFIXES).
    source_id = f"file:{str(args.get('path', 'unknown'))[:120]}"
    text = output.get("text", "")
    if text:
        return make_evidence(kind="file", source_id=source_id, obtained_via="convert_file",
                             claim=text, excerpt=text)
    outputs = output.get("outputs") or []
    if not outputs:
        return None
    produced = str(outputs[0])
    return make_evidence(kind="file", source_id=source_id, obtained_via="convert_file",
                         claim=produced, excerpt=produced)


def _file_write_evidence(args: dict[str, Any], output: Any) -> Evidence | None:
    """Evidence of a performed write: path, mode, bytes and what was passed to write."""
    if not isinstance(output, dict):
        return None
    path = str(output.get("path") or args.get("path") or "")[:120]
    written = output.get("bytes_written")
    if not path or not isinstance(written, int):
        return None
    # The witness is the tool run, not the model's words: without it a true "file created" has nothing to cite.
    receipt = f"file_write success path={path} mode={output.get('mode') or ''} bytes_written={written}"
    if output.get("backup_path"):
        receipt += f" backup_path={output['backup_path']}"
    excerpt = receipt
    content = args.get("content")
    # The passed text is named only if it matches the tool's byte count.
    if isinstance(content, str) and len(content.encode("utf-8")) == written:
        from core.redaction import redact_payload

        excerpt += f"\ncontent passed to the tool (not re-read from disk):\n{redact_payload(content)}"
    return make_evidence(kind="tool_output", source_id=f"tool_output:file_write:{path}",
                         obtained_via="file_write", claim=receipt, excerpt=excerpt)


def _flagged_string_evidence(tool_name: str, args: dict[str, Any], output: Any) -> Evidence | None:
    """Evidence from output the scanner flagged and turned into a wrapper string.

    For tools without an envelope (`_UNTRUSTED_OUTPUT_FIELDS`) the flag turns a dict into a string; the evidence is kept, the excerpt is the whole wrapper with the warning, so its sentences count as `suspect`.
    """
    if not isinstance(output, str) or not carries_suspicious_annotation(output):
        return None
    if tool_name == "python_probe":
        kind, source_id, conf, origin = "tool_output", "tool_output:python_probe", None, None
    elif tool_name == "read_logs":
        kind, source_id, conf, origin = "log_event", f"log_event:{args.get('trace_id', '')}:flagged", None, None
    elif tool_name == "diff_file":
        kind, source_id, conf, origin = "diff_preview", f"diff_preview:{args.get('path', '<unknown>')}", None, None
    elif tool_name == "rss_fetch":
        kind, source_id, conf, origin = "web_page", f"web_page:{args.get('url', '<unknown>')}", 0.68, None
    # Same kind/source_id as the clean branches so citations find the evidence.
    elif tool_name == "web_search":
        kind, source_id, conf, origin = "web_search_hit", f"web_search:{args.get('query', '<unknown>')}", None, None
    elif tool_name == "convert_file":
        kind, source_id, conf, origin = "file", f"file:{str(args.get('path', 'unknown'))[:120]}", None, None
    elif tool_name == "file_write":
        path = str(args.get("path") or "")[:120]
        if not path:
            return None
        kind, source_id, conf, origin = "tool_output", f"tool_output:file_write:{path}", None, None
    elif tool_name == "memory_recall":
        # Whose records these are cannot be known from the wrapper, so not a human's.
        kind, source_id = "tool_output", "tool_output:memory_recall"
        conf, origin = DEFAULT_CONFIDENCE["memory"], "agent-auto"
    else:
        return None
    return make_evidence(
        kind=kind,
        source_id=source_id,
        obtained_via=tool_name,
        claim=f"Tool {tool_name} returned output flagged by the injection scanner",
        excerpt=output,
        confidence=conf,
        origin=origin,
    )


def evidence_from_tool_result(  # noqa: PLR0911, PLR0912, PLR0915 — flat: depth 4, all 34 returns are guard clauses
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    output: Any,
    status: str = "success",
) -> Evidence | None:
    """Typed evidence from a tool output, or None (error/empty/action tool is not a source). Bad output never crashes the cycle."""
    if status != "success":
        return None
    if not isinstance(tool_name, str) or not tool_name:
        return None

    args = arguments or {}

    flagged = _flagged_string_evidence(tool_name, args, output)
    if flagged is not None:
        return flagged

    if tool_name == "python_probe":
        return _python_probe_evidence(args, output)

    if tool_name == "file_read":
        return _file_read_evidence(args, output)
    if tool_name == "convert_file":
        return _convert_file_evidence(args, output)

    if tool_name == "web_search":
        if not isinstance(output, list):
            return None
        if not output:
            # An empty search is not evidence; ReplanPolicy picks it up as `web_empty`.
            return None
        query = str(args.get("query", "<unknown>"))
        # Excerpt = each hit's title + url; snippets are never trusted, but the listing is useful provenance.
        lines: list[str] = []
        for hit in output[:10]:
            if not isinstance(hit, dict):
                continue
            title = str(hit.get("title", "")).strip()
            url = str(hit.get("url", "")).strip()
            if title or url:
                lines.append(f"- {title} <{url}>")
        if not lines:
            return None
        return make_evidence(
            kind="web_search_hit",
            source_id=f"web_search:{query}",
            obtained_via="web_search",
            claim=f"Search for {query!r} returned {len(output)} hit(s)",
            excerpt="\n".join(lines),
            source_text="\n".join(lines),
        )

    if tool_name == "web_fetch":
        if not isinstance(output, dict):
            return None
        url = str(output.get("url") or args.get("url", "<unknown>"))
        text = output.get("text", "")
        if not isinstance(text, str) or not text:
            return None
        # An error page is no source for any claim; cut here at the evidence producer, not by a second judge beside the verifier.
        if looks_like_error_page(text) and output.get("not_found") is not True:  # 404: verifier_absence
            return None
        ch = output.get("content_hash")
        ev = make_evidence(
            kind="web_page",
            source_id=f"web_page:{url}",
            obtained_via="web_fetch",
            claim=f"Fetched page {url}",
            excerpt=text,
            fetched_at=output.get("fetched_at"),
            source_text=_page_body(text),
        )
        # The tool's hash covers the whole page, not the excerpt.
        return replace(ev, content_hash=ch) if isinstance(ch, str) and ch else ev

    if tool_name == "rss_fetch":
        if not isinstance(output, dict):
            return None
        url = str(output.get("url") or args.get("url", "<unknown>"))
        entries = output.get("entries", [])
        if not isinstance(entries, list) or not entries:
            return None
        lines: list[str] = []
        for item in entries[:10]:
            if not isinstance(item, dict):
                continue
            title = str(item.get("title", "")).strip()
            link = str(item.get("url", "")).strip()
            published = str(item.get("published_at", "")).strip()
            if title or link:
                bits = [title]
                if link:
                    bits.append(f"<{link}>")
                if published:
                    bits.append(f"({published})")
                lines.append(" ".join(part for part in bits if part))
        if not lines:
            return None
        ev = make_evidence(
            kind="web_page",
            source_id=f"web_page:{url}",
            obtained_via="rss_fetch",
            claim=f"Fetched RSS/Atom feed {url}",
            excerpt="\n".join(lines),
            fetched_at=output.get("fetched_at"),
            confidence=0.68,
        )
        ch = output.get("content_hash")
        if isinstance(ch, str) and ch:
            return Evidence(
                id=ev.id, kind=ev.kind, source_id=ev.source_id,
                obtained_via=ev.obtained_via, content_hash=ch,
                fetched_at=ev.fetched_at, confidence=ev.confidence,
                claim=ev.claim, excerpt=ev.excerpt,
            )
        return ev

    if tool_name == "run_tests":
        if not isinstance(output, dict):
            return None
        passed = output.get("passed", 0)
        failed = output.get("failed", 0)
        errors = output.get("errors", 0)
        timed_out = bool(output.get("timed_out", False))
        verdict = (
            f"timed_out={timed_out}" if timed_out
            else f"passed={passed}, failed={failed}, errors={errors}"
        )
        # Citation-friendly source_id with the plan's tool name and the requested test paths (not the full argv).
        # Shape: test_result:run_tests:pytest:<paths-comma-joined>
        paths = args.get("paths")
        if isinstance(paths, list) and paths:
            target = ",".join(str(p) for p in paths)[:60]
        elif isinstance(paths, str) and paths:
            target = paths[:60]
        else:
            target = "tests"  # tool's documented default target
        source_id = f"test_result:run_tests:pytest:{target}"
        return make_evidence(
            kind="test_result",
            source_id=source_id,
            obtained_via="run_tests",
            claim=f"Test run verdict: {verdict}",
            excerpt=str(output.get("stdout_tail", ""))[:MAX_EXCERPT_CHARS],
        )

    if tool_name == "read_logs":
        if not isinstance(output, dict):
            return None
        trace = str(output.get("trace_id", ""))
        events = output.get("events", []) or []
        if not isinstance(events, list):
            return None
        if not events:
            # Empty logs can still inform ("nothing went wrong in window X") but are weak: a low-confidence record.
            return make_evidence(
                kind="log_event",
                source_id=f"log_event:{trace}:empty",
                obtained_via="read_logs",
                claim=f"No events in trace {trace}",
                excerpt="",
                confidence=0.30,
            )
        # Event content, not only its name (a bare "error: <ts>" dropped the payload). Volume is bounded in make_evidence.
        excerpt_lines: list[str] = []
        for ev in events[:20]:
            if not isinstance(ev, dict):
                continue
            try:
                excerpt_lines.append(json.dumps(ev, ensure_ascii=False, default=str))
            except (TypeError, ValueError):
                excerpt_lines.append(f"{ev.get('event', '?')}: {ev.get('ts', '')}")
        return make_evidence(
            kind="log_event",
            source_id=f"log_event:{trace}:{len(events)}",
            obtained_via="read_logs",
            claim=f"Read {len(events)} event(s) from trace {trace}",
            excerpt="\n".join(excerpt_lines),
        )

    if tool_name == "shell_exec":
        if not isinstance(output, dict):
            return None
        argv = output.get("argv") or output.get("command") or []
        cmd_str = " ".join(argv) if isinstance(argv, list) else str(argv)
        if not cmd_str:
            return None
        exit_code = output.get("exit_code")
        stdout = output.get("stdout", "")
        stderr = output.get("stderr", "")
        excerpt = (
            (stdout if isinstance(stdout, str) else "") + "\n"
            + (stderr if isinstance(stderr, str) else "")
        ).strip()
        # source_id includes the tool name and the basename of argv[0] so citations like `[shell:python]` match.
        # Shape: shell_output:shell_exec:<argv0_basename>:<short_cmd>
        first_arg = ""
        if isinstance(argv, list) and argv:
            first = str(argv[0])
            # Strip drive letters / directory parts (POSIX and Windows).
            first_arg = first.replace("\\", "/").rsplit("/", 1)[-1]
            # Drop trailing `.exe` so `[shell:python]` matches `python.exe`.
            if first_arg.lower().endswith(".exe"):
                first_arg = first_arg[:-4]
        first_arg = first_arg or "cmd"
        short_cmd = cmd_str[:60]
        source_id = f"shell_output:shell_exec:{first_arg}:{short_cmd}"
        own = (_shell_touches_own_state(argv, args.get("command"), args.get("argv"), args.get("args"))
               or _search_touches_own_state(None, excerpt))
        return make_evidence(
            kind="shell_output",
            origin="agent-own" if own else None,
            source_id=source_id,
            obtained_via="shell_exec",
            claim=f"Ran `{cmd_str[:80]}`, exit_code={exit_code}",
            excerpt=excerpt or "<no output>",
        )

    if tool_name == "diff_file":
        if not isinstance(output, dict):
            return None
        path = str(args.get("path", output.get("path", "<unknown>")))
        adds = output.get("additions", 0)
        dels = output.get("deletions", 0)
        diff = output.get("diff", "")
        return make_evidence(
            kind="diff_preview",
            source_id=f"diff_preview:{path}",
            obtained_via="diff_file",
            claim=f"Proposed change to {path}: +{adds} -{dels}",
            excerpt=str(diff) if isinstance(diff, str) else "",
        )

    if tool_name == "file_write":
        return _file_write_evidence(args, output)

    if tool_name == "list_dir":
        if not isinstance(output, str) or not output:
            return None
        path = str(args.get("path", "."))
        return make_evidence(
            kind="file",
            origin="agent-own" if _is_own_state_path(path) else None,
            source_id=f"file:{path}",
            obtained_via="list_dir",
            claim=f"Directory listing of workspace path {path}",
            excerpt=output,
        )

    if tool_name == "find_in_files":
        if not isinstance(output, str) or not output:
            return None
        own = _search_touches_own_state(args.get("path", "."), output)
        return make_evidence(
            kind="tool_output",
            origin="agent-own" if own else None,
            source_id="tool_output:find_in_files",
            obtained_via="find_in_files",
            claim=f"Workspace search under {args.get('path', '.')}",
            excerpt=_search_excerpt(output),
        )

    if tool_name == "memory_recall":
        return _memory_recall_evidence(args, output)

    # Generic tool_output evidence so the chain does not lose track; confidence is conservative.
    try:
        excerpt = repr(output)
    except Exception:  # noqa: BLE001 — reason stated above
        # No journaling here: this IS the failure report; the placeholder says so in the text a reader sees.
        excerpt = "<unserialisable tool output>"
    if not excerpt or excerpt in ("None", "''"):
        return None
    return make_evidence(
        kind="tool_output",
        source_id=f"tool_output:{tool_name}",
        obtained_via=tool_name,
        claim=f"Tool {tool_name} returned a result",
        excerpt=excerpt,
    )



def evidence_from_user_directive(*, directive: str, request_id: str) -> Evidence:
    """User said something explicitly: the strongest source."""
    return make_evidence(
        kind="user_explicit",
        source_id=f"user_explicit:{request_id}",
        obtained_via="user_explicit",
        claim="User explicitly directed",
        excerpt=directive,
    )


def evidence_from_memory_record(
    *,
    record_id: str,
    content: str,
    source: str | None,
    created_at: str | None,
) -> Evidence:
    """Memory record retrieved during planning."""
    conf = DEFAULT_CONFIDENCE["memory"]
    if not source:
        conf = max(0.25, conf - 0.15)
    return make_evidence(
        kind="memory",
        source_id=f"memory:{record_id}",
        obtained_via="memory",
        claim=f"Memory record {record_id}" + (f" (source={source})" if source else ""),
        excerpt=content,
        confidence=conf,
        # Structured copy of the fact so the verifier can weigh provenance without parsing the sentence.
        origin=source,
        fetched_at=created_at,
    )


def evidence_from_sensor_block(*, name: str, content: str) -> Evidence:
    """A block the loop injected from its own sensors (model roster, spend mirror), cited as `[sensor:<name>]` with the loop as origin."""
    return make_evidence(
        kind="sensor",
        source_id=f"sensor:{name}",
        obtained_via="sensor",
        claim=f"Sensor block {name}, read by the loop from its own journals",
        excerpt=content,
        confidence=0.9,
        origin="loop_sensor",
    )


def evidence_from_prior_turn(
    *,
    turn_id: str,
    turn_index: int,
    question: str,
    answer: str,
) -> Evidence:
    """One earlier turn of THIS session, verbatim.

    A recording, admitted so a conversational-correction turn can support statements about the preceding exchange. A claim resolved against it becomes ``dialogue_supported``, never ``verified``, and only when the claim is itself about the dialogue.
    """
    body = (
        f"user asked: {question.strip()}\n"
        f"agent answered: {answer.strip()}"
    )
    return make_evidence(
        kind="session_dialogue",
        source_id=f"session_dialogue:turn_{turn_index}:{turn_id}",
        obtained_via="working_memory",
        claim=f"Verbatim turn {turn_index} of the current session",
        excerpt=body,
        origin="session",
    )


def evidence_from_llm_claim(*, claim_text: str, model: str | None = None) -> Evidence:
    """Last-resort: LLM produced an assertion without external grounding.

    The Verifier flags claims tied only to these as `[unverified]`.
    """
    return make_evidence(
        kind="llm_claim",
        source_id=f"llm_claim:{model or 'unknown'}",
        obtained_via=f"llm:{model}" if model else "llm",
        claim="LLM-generated assertion without external source",
        excerpt=claim_text,
    )
