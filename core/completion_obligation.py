"""Did this cycle incur an obligation to observe or act, and leave it unmet?

Replaces the keyword detector in :mod:`core.termination_guard` as the *source of
truth* for premature completion. That detector asked "does the user's wording
contain a tool-ish word?", and measurement showed what that costs
(`docs/audit/SENSOR_SIGNAL_MEASUREMENT.md`): it recognised **1 of 12** phrasings
that unambiguously demand a tool, and it fired on *«объясни разницу между REST и
GraphQL»* because `"разниц"` is a keyword for the diff tool. Wording is not the
obligation; it is one weak proxy for it.

The question this module asks instead:

    Did some part of this cycle create a duty to observe the world or to run
    something — and was that duty left unsatisfied *without saying so*?

## Four sources, three of them wired

``intent``
    Structural, object-based, and deliberately **not** a verb list: a question
    that names a workspace path, or a turn carrying a ``file_hint``, incurs a
    workspace-observation duty. Keying on the *object referenced* rather than on
    the phrasing is what makes «объясни разницу…» a non-event — it names no
    file — while «прочитай core/foo.py» is one regardless of the verb used.

``plan``
    Only **admitted** plan steps, never the planner's raw output. A step that
    reached execution for an observing tool is a duty to produce a result.

``freshness``
    The existing ``realtime_required`` from the source ranker.

``acceptance_criteria``
    **not_wired.** ``Goal.success_criteria`` and ``PlanStep.expected_outcome``
    exist as free-text fields, but no criterion is carried through the cycle,
    tied to an obligation, given a verification method, or allowed to affect
    completion. So this module reports the source as *unavailable* and draws no
    conclusion from it. `not_wired` is not `no_requirement`: an unwired source
    must not create a duty, must not cancel one, and must not count as a passed
    check. Wiring it is a separate architectural change — a contract between
    task statement, completion check and outcome — not a detail of this sensor.

## The rule

One wired source is enough. Sources do not vote: ``realtime_required=False``
cannot cancel a duty that ``intent`` or ``plan`` created.

    triggered  iff  some obligation is `silently_missing`

An obligation the run failed to meet **and said so** is not premature
completion; it is an honest report of a failure. That is why the four states are
distinguished, and why only the silent one fires.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Iterable, Literal, Sequence

from core.file_request_intent import extract_path_mentions

ObligationSource = Literal["intent", "plan", "freshness", "acceptance_criteria"]

RequirementKind = Literal[
    "workspace_observation",
    "tool_execution",
    "fresh_evidence",
]

ObligationStatus = Literal[
    "satisfied",
    "failed_but_reported",
    "blocked_and_disclosed",
    "silently_missing",
]

#: Sources this module can actually read today. `acceptance_criteria` is absent
#: on purpose and is reported through `unavailable_sources`.
WIRED_SOURCES: tuple[ObligationSource, ...] = ("intent", "plan", "freshness")
UNWIRED_SOURCES: tuple[ObligationSource, ...] = ("acceptance_criteria",)

#: Tools whose whole purpose is to bring an observation back. A plan step using
#: one of these is a duty to produce a result; `file_write` and friends are
#: actions, not observations, and are not obligations of this kind.
_OBSERVING_TOOLS: frozenset[str] = frozenset({
    "file_read", "list_dir", "read_logs", "run_tests",
    "web_search", "web_fetch", "rss_fetch", "shell_exec", "diff_file",
})

#: A path named in the question. Same shape the loop already uses for its
#: file-scope notice — the object, not the verb.
#:
#: The regex this used until 2026-08-02 carried a lookbehind guard measured in
#: commit 113bd88 (4.7 s -> 2 ms on separator walls) — and CodeQL alert #5 was
#: still right: starts after LETTERS remained open, so a wall of class
#: characters cost 3.7 s at 16 KB of free text the model itself can emit. The
#: same finding was fixed for the loop's twin pattern first (alert #11); this
#: site now delegates to that shared linear scanner, with this module's wider
#: extension list. The retired pattern lives verbatim in the test file as the
#: equivalence oracle.
_OBLIGATION_PATH_EXTS: tuple[str, ...] = (
    "py", "md", "txt", "json", "yml", "yaml", "pdf",
    "jsonl", "toml", "ini", "cfg", "log",
)


def paths_mentioned(text: str) -> tuple[str, ...]:
    """Workspace paths named in the text, de-duplicated, order preserved."""
    return tuple(
        extract_path_mentions(text or "", exts=_OBLIGATION_PATH_EXTS)
    )


@dataclass(frozen=True)
class Obligation:
    """One duty this cycle incurred, and what became of it."""

    source: ObligationSource
    kind: RequirementKind
    status: ObligationStatus
    detail: str = ""

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "kind": self.kind,
            "status": self.status,
            "detail": self.detail,
        }


@dataclass(frozen=True)
class CompletionObligationResult:
    """Whether a duty existed, whether it was met, and on whose authority."""

    required: bool
    satisfied: bool
    triggered: bool
    requirement_sources: tuple[str, ...] = ()
    missing_requirements: tuple[str, ...] = ()
    unavailable_sources: tuple[str, ...] = UNWIRED_SOURCES
    obligations: tuple[Obligation, ...] = ()
    notes: tuple[str, ...] = field(default_factory=tuple)

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "satisfied": self.satisfied,
            "triggered": self.triggered,
            "requirement_sources": list(self.requirement_sources),
            "missing_requirements": list(self.missing_requirements),
            # Reported on every turn, satisfied or not: "we could not consult
            # this source" is a different statement from "this source said
            # nothing was required", and the log has to keep them apart.
            "unavailable_sources": list(self.unavailable_sources),
            "obligations": [o.to_log_payload() for o in self.obligations],
            "notes": list(self.notes),
        }


def _step_tool(step: Any) -> str:
    spec = getattr(step, "action_spec", None)
    if isinstance(spec, dict):
        return str(spec.get("tool_name") or "")
    if isinstance(step, dict):
        return str((step.get("action_spec") or {}).get("tool_name") or "")
    return ""


def _step_status(step: Any) -> str:
    status = getattr(step, "status", None)
    if status is None and isinstance(step, dict):
        status = step.get("status")
    return str(status or "")


def _discloses(answer: str, *needles: str) -> bool:
    """Does the user-facing answer actually mention this?

    Checked against the composed answer rather than inferred from run state:
    "the run recorded a failure" and "the user was told" are different facts,
    and only the second one makes a missing observation honest.
    """
    text = (answer or "").casefold()
    if not text:
        return False
    return any(n and n.casefold() in text for n in needles)


def evaluate_completion_obligations(
    *,
    question: str,
    answer: str,
    plan_steps: Sequence[Any] = (),
    artifacts: dict[str, Any] | None = None,
    chain_size: int = 0,
    realtime_required: bool = False,
    file_hint: str | None = None,
    failure_codes: Iterable[str] = (),
    denied_tools: Iterable[str] = (),
) -> CompletionObligationResult:
    """Collect obligations from the wired sources and judge each one.

    Pure: no I/O, no LLM, no store access. ``answer`` is the composed,
    user-facing text, so disclosure is read from what the operator actually
    receives.
    """
    artifacts = artifacts or {}
    artifact_tools = {
        str((meta or {}).get("tool") or "") for meta in artifacts.values()
    }
    artifact_blob = " ".join(str(k) for k in artifacts)
    failure_codes = tuple(str(c) for c in failure_codes if c)
    denied = {str(t).strip() for t in denied_tools if t}

    obligations: list[Obligation] = []

    # ── intent ───────────────────────────────────────────────────────────
    # Object-based: a named path, or a turn-scoped file hint.
    named = paths_mentioned(question)
    if file_hint and str(file_hint).strip():
        named = tuple(dict.fromkeys(named + (str(file_hint).strip(),)))
    for path in named:
        base = path.replace("\\", "/").rsplit("/", 1)[-1]
        seen = base.casefold() in artifact_blob.casefold()
        obligations.append(
            Obligation(
                source="intent",
                kind="workspace_observation",
                status="satisfied" if seen else (
                    "failed_but_reported"
                    if failure_codes and _discloses(answer, base, *failure_codes)
                    else "silently_missing"
                ),
                detail=path,
            )
        )

    # ── plan ─────────────────────────────────────────────────────────────
    # Admitted steps only. A step that ran an observing tool owes a result.
    for step in plan_steps:
        tool = _step_tool(step)
        if tool not in _OBSERVING_TOOLS:
            continue
        status = _step_status(step)
        if tool in artifact_tools or status == "done":
            state: ObligationStatus = "satisfied"
        elif tool in denied:
            state = (
                "blocked_and_disclosed"
                if _discloses(answer, tool, "approval", "policy", "denied",
                              "запрещ", "одобрен", "политик")
                else "silently_missing"
            )
        elif status == "failed" or failure_codes:
            state = (
                "failed_but_reported"
                if _discloses(answer, tool, *failure_codes)
                else "silently_missing"
            )
        else:
            state = "silently_missing"
        obligations.append(
            Obligation(
                source="plan",
                kind="tool_execution",
                status=state,
                detail=tool,
            )
        )

    # ── freshness ────────────────────────────────────────────────────────
    # A positive source only. `realtime_required=False` says nothing about the
    # duties the other sources found, and must never cancel them.
    if realtime_required:
        fresh = chain_size > 0 or bool(artifacts)
        obligations.append(
            Obligation(
                source="freshness",
                kind="fresh_evidence",
                status="satisfied" if fresh else (
                    "failed_but_reported"
                    if failure_codes and _discloses(answer, *failure_codes)
                    else "silently_missing"
                ),
                detail="realtime_required",
            )
        )

    sources = tuple(dict.fromkeys(o.source for o in obligations))
    missing = tuple(
        dict.fromkeys(
            o.kind for o in obligations if o.status == "silently_missing"
        )
    )
    required = bool(obligations)
    satisfied = required and all(o.status == "satisfied" for o in obligations)
    triggered = any(o.status == "silently_missing" for o in obligations)

    notes: list[str] = []
    if not required:
        notes.append("no wired source created an obligation")
    if triggered:
        notes.append("an obligation was left unmet without disclosure")

    return CompletionObligationResult(
        required=required,
        satisfied=satisfied,
        triggered=triggered,
        requirement_sources=sources,
        missing_requirements=missing,
        unavailable_sources=UNWIRED_SOURCES,
        obligations=tuple(obligations),
        notes=tuple(notes),
    )
