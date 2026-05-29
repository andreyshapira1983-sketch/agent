"""MVP-14.1 — Evidence + Provenance model.

The hard rule of this layer: **LLM is not a source of truth.** A claim
the agent makes in its final answer must be tied back to a typed
`Evidence` record — a file, a tool result, a fetched web page, a log
event, a memory record explicitly tagged by the user, or, at worst,
an unverified `llm_claim`. Verifier (MVP-14.4) uses this chain to
annotate the answer with `[verified:...]` / `[unverified]` markers
and to surface conflicts.

Design choices pinned by the test suite:

  - Evidence is built OUTSIDE the tool. Tools keep their existing
    contracts; the AgentLoop inspects (tool_name, output) and produces
    the typed record via :func:`evidence_from_tool_result`. This means
    every tool's existing 30-100 unit tests stay valid — we only add a
    parallel structure, never mutate the old one.
  - One tool result → at most ONE Evidence. `web_search` collapses to a
    single "top-N hits" record, `read_logs` to a single "N events"
    record. Per-hit / per-event evidence is deferred until the Verifier
    proves we need finer granularity.
  - A failed `ToolResult` (status != success) yields no Evidence. An
    error is the absence of a source, not a weaker source.
  - `content_hash` is sha256 of the excerpt — a stable handle for
    "same document?" reasoning that doesn't require keeping a full
    file copy around.
  - Confidence is a number in [0, 1] derived from a baseline table.
    Modifiers (freshness decay for web pages, domain trust, presence
    of secrets) will be applied in MVP-14.4 / a future SourceRanker;
    the model here stores only the post-modifier value.
"""
from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Literal

from core.ids import new_id


# ---------------------------------------------------------------------------
# Taxonomy
# ---------------------------------------------------------------------------

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
    "user_explicit",    # :remember / explicit consent / direct user command
    "llm_claim",        # LLM-generated text WITHOUT external grounding
    "unknown",          # last-resort bucket
]

ALL_EVIDENCE_KINDS: tuple[EvidenceKind, ...] = (
    "file", "web_page", "web_search_hit", "tool_output", "test_result",
    "log_event", "shell_output", "diff_preview", "memory",
    "user_explicit", "llm_claim", "unknown",
)


# Baseline confidence per source kind. These are the "all else equal"
# scores. The Verifier applies modifiers (freshness decay, domain trust,
# secret presence, conflict resolution) on top.
#
# Reading order = source hierarchy from the user spec:
#   user-explicit > tested code > workspace file > log > shell
#   > diff preview > fetched web page > generic tool output
#   > memory (uncertain provenance) > web-search pointer
#   > llm-claim > unknown
DEFAULT_CONFIDENCE: dict[EvidenceKind, float] = {
    "user_explicit":    1.00,
    "test_result":      0.95,
    "file":             0.90,
    "log_event":        0.90,
    "shell_output":     0.85,
    "diff_preview":     0.80,
    "web_page":         0.75,
    "tool_output":      0.70,
    "memory":           0.55,
    "web_search_hit":   0.35,
    "llm_claim":        0.20,
    "unknown":          0.10,
}

# Maximum excerpt length stored on disk / sent into prompts. Keeps audit
# log entries bounded even when a tool returns megabytes of text.
MAX_EXCERPT_CHARS = 800


# ---------------------------------------------------------------------------
# Core dataclasses
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Evidence:
    """One immutable piece of supporting evidence for a claim.

    Frozen on purpose: an Evidence record is an audit artefact. Once
    issued, neither the loop nor the planner should be able to mutate
    it. Modifications produce a new Evidence (with its own id +
    fetched_at).
    """
    id: str
    kind: EvidenceKind
    source_id: str           # path / URL / trace_id#event / mem_xxx / req_xxx
    obtained_via: str        # tool name, "memory", or "user_explicit"
    content_hash: str        # sha256 of excerpt
    fetched_at: str          # ISO-8601 UTC
    confidence: float        # in [0, 1]
    claim: str               # short human-readable claim this evidence supports
    excerpt: str             # first MAX_EXCERPT_CHARS chars of the source

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
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "Evidence":
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
        )


@dataclass
class ProvenanceChain:
    """Per-cycle ordered collection of Evidence.

    Order matters: the Verifier prefers the earliest evidence that
    matches a given claim (the first thing a tool returned should be
    the primary source, later evidence is supporting). Equal-rank
    matches are tie-broken by confidence.
    """
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


# ---------------------------------------------------------------------------
# Hashing & helpers
# ---------------------------------------------------------------------------

def compute_content_hash(text: str) -> str:
    """sha256 hex of the utf-8 bytes. errors='replace' so a non-UTF-8
    blob still gets a stable hash instead of crashing the loop."""
    return hashlib.sha256(text.encode("utf-8", errors="replace")).hexdigest()


def _now_utc_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


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
) -> Evidence:
    """Constructor that fills in id / hash / timestamp / baseline confidence.

    Use this directly when you have raw inputs; use the factory below
    when you have a (tool_name, output) pair from the loop.
    """
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
    )


# ---------------------------------------------------------------------------
# Factory: (tool_name, output) -> Evidence
# ---------------------------------------------------------------------------

def evidence_from_tool_result(
    *,
    tool_name: str,
    arguments: dict[str, Any] | None,
    output: Any,
    status: str = "success",
) -> Evidence | None:
    """Inspect a tool's output and produce a typed Evidence.

    Returns None when:
      * the tool reported a non-success status (an error is the absence
        of a source);
      * the output shape can't form a meaningful evidence record (empty
        result, malformed dict);
      * the tool is one we deliberately don't credit as a source (e.g.
        `file_write` is an action, not a source of truth — it has its
        own audit event already).

    This function is intentionally defensive: a malformed `output` that
    somehow slipped past `validate_output` must NOT crash the loop. It
    returns None and lets the loop continue.
    """
    if status != "success":
        return None
    if not isinstance(tool_name, str) or not tool_name:
        return None

    args = arguments or {}

    # ---- file_read --------------------------------------------------------
    if tool_name == "file_read":
        if not isinstance(output, str) or not output:
            return None
        path = str(args.get("path", "<unknown>"))
        return make_evidence(
            kind="file",
            source_id=f"file:{path}",
            obtained_via="file_read",
            claim=f"Contents of workspace file {path}",
            excerpt=output,
        )

    # ---- web_search -------------------------------------------------------
    if tool_name == "web_search":
        if not isinstance(output, list):
            return None
        if not output:
            # An empty search is not evidence — it's a failure mode the
            # ReplanPolicy will pick up as `web_empty`. Returning None
            # here keeps the two systems in sync.
            return None
        query = str(args.get("query", "<unknown>"))
        # Excerpt = each hit's title + url, concatenated. The Verifier
        # never trusts a snippet — but the listing is useful for "we
        # looked, here's what we saw" provenance.
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
        )

    # ---- web_fetch (MVP-14.2) --------------------------------------------
    if tool_name == "web_fetch":
        if not isinstance(output, dict):
            return None
        url = str(output.get("url") or args.get("url", "<unknown>"))
        text = output.get("text", "")
        if not isinstance(text, str) or not text:
            return None
        ch = output.get("content_hash")
        # Trust the tool's own hash when present — it covers more bytes
        # than the truncated excerpt we'll store here.
        ev = make_evidence(
            kind="web_page",
            source_id=f"web_page:{url}",
            obtained_via="web_fetch",
            claim=f"Fetched page {url}",
            excerpt=text,
            fetched_at=output.get("fetched_at"),
        )
        if isinstance(ch, str) and ch:
            return Evidence(
                id=ev.id, kind=ev.kind, source_id=ev.source_id,
                obtained_via=ev.obtained_via, content_hash=ch,
                fetched_at=ev.fetched_at, confidence=ev.confidence,
                claim=ev.claim, excerpt=ev.excerpt,
            )
        return ev

    # ---- rss_fetch --------------------------------------------------------
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

    # ---- run_tests --------------------------------------------------------
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
        # Build a citation-friendly source_id that includes BOTH the tool
        # name the LLM uses in its plans (`run_tests`) AND the literal
        # test target paths it asked for. The full argv (e.g. the
        # absolute `python.exe` path on Windows) is too noisy for
        # citation matching — the LLM cites things like `[test:pytest]`
        # or `[test:tests/bug_lab]`, not the full python path.
        #
        # Shape:  test_result:run_tests:pytest:<paths-comma-joined>
        # Example: test_result:run_tests:pytest:tests/bug_lab,tests/core
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

    # ---- read_logs --------------------------------------------------------
    if tool_name == "read_logs":
        if not isinstance(output, dict):
            return None
        trace = str(output.get("trace_id", ""))
        events = output.get("events", []) or []
        if not isinstance(events, list):
            return None
        if not events:
            # Empty logs CAN still be informative ("nothing went wrong
            # in window X") — but they're weak evidence. We surface them
            # as a low-confidence record so the planner can still cite
            # the read.
            return make_evidence(
                kind="log_event",
                source_id=f"log_event:{trace}:empty",
                obtained_via="read_logs",
                claim=f"No events in trace {trace}",
                excerpt="",
                confidence=0.30,
            )
        excerpt_lines: list[str] = []
        for ev in events[:20]:
            if isinstance(ev, dict):
                excerpt_lines.append(f"{ev.get('event','?')}: {ev.get('ts','')}")
        return make_evidence(
            kind="log_event",
            source_id=f"log_event:{trace}:{len(events)}",
            obtained_via="read_logs",
            claim=f"Read {len(events)} event(s) from trace {trace}",
            excerpt="\n".join(excerpt_lines),
        )

    # ---- shell_exec -------------------------------------------------------
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
        # Source_id includes the tool name (`shell_exec`) AND the
        # basename of argv[0] (the program the LLM actually thinks
        # about: `python`, `git`, `pytest`, ...) so substring matches
        # for typical citations like `[shell:python]` or
        # `[shell:git]` succeed. The remainder of the command line
        # (truncated) is preserved for forensic context.
        # Shape: shell_output:shell_exec:<argv0_basename>:<short_cmd>
        first_arg = ""
        if isinstance(argv, list) and argv:
            first = str(argv[0])
            # Strip drive letters / directory parts — keep the bare
            # program name. Works on both POSIX and Windows paths.
            first_arg = first.replace("\\", "/").rsplit("/", 1)[-1]
            # Drop trailing `.exe` so `[shell:python]` matches
            # `python.exe` on Windows.
            if first_arg.lower().endswith(".exe"):
                first_arg = first_arg[:-4]
        first_arg = first_arg or "cmd"
        short_cmd = cmd_str[:60]
        source_id = f"shell_output:shell_exec:{first_arg}:{short_cmd}"
        return make_evidence(
            kind="shell_output",
            source_id=source_id,
            obtained_via="shell_exec",
            claim=f"Ran `{cmd_str[:80]}`, exit_code={exit_code}",
            excerpt=excerpt or "<no output>",
        )

    # ---- diff_file --------------------------------------------------------
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

    # ---- file_write -------------------------------------------------------
    # By design: a write is an action, not a source of truth. The audit
    # event records it; evidence about file contents must come from a
    # subsequent `file_read`. Return None here on purpose so the loop
    # never gives a write-claim the same weight as a read-claim.
    if tool_name == "file_write":
        return None

    # ---- unknown tool fallback -------------------------------------------
    # Emit a generic tool_output evidence so the chain doesn't lose track
    # of what happened. Confidence is conservative; the Verifier will
    # treat this as weak.
    try:
        excerpt = repr(output)
    except Exception:
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


# ---------------------------------------------------------------------------
# Convenience constructors for non-tool sources
# ---------------------------------------------------------------------------

def evidence_from_user_directive(*, directive: str, request_id: str) -> Evidence:
    """User said something explicitly — the strongest source we have."""
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
    """Memory record retrieved during planning.

    Confidence is reduced when the record has no `source` (no
    provenance) — a memory entry with unknown origin can't be trusted
    as much as one with a clear chain.
    """
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
        fetched_at=created_at,
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
