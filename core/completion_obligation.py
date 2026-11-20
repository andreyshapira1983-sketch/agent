"""Detect obligations to observe or act that this cycle incurred and left silently unmet."""
from __future__ import annotations

import re
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from typing import Any, Literal

from core.file_request_intent import OBLIGATION_PATH_EXTS, paths_mentioned

ObligationSource = Literal["intent", "plan", "freshness", "acceptance_criteria"]

RequirementKind = Literal[
    "workspace_observation",
    "tool_execution",
    "fresh_evidence",
    "deliverable_produced",
]

ObligationStatus = Literal[
    "satisfied",
    "failed_but_reported",
    "blocked_and_disclosed",
    "silently_missing",
]

#: Sources readable today; acceptance_criteria is absent on purpose (reported via unavailable_sources).
WIRED_SOURCES: tuple[ObligationSource, ...] = ("intent", "plan", "freshness")
UNWIRED_SOURCES: tuple[ObligationSource, ...] = ("acceptance_criteria",)

#: Tools whose purpose is to return an observation; writes are actions, not this kind of obligation.
_OBSERVING_TOOLS: frozenset[str] = frozenset({
    "file_read", "list_dir", "read_logs", "run_tests",
    "web_search", "web_fetch", "rss_fetch", "shell_exec", "diff_file",
    "find_in_files",
})

#: Extensions for paths named in the question (wider than the loop's list), scanned by the
#: shared linear scanner via paths_mentioned.
#: Owned by core/file_request_intent.py so completion_contract shares it without an import
#: cycle; re-exported here for old import sites.
_OBLIGATION_PATH_EXTS = OBLIGATION_PATH_EXTS


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
    #: complete | partial: coverage of the operator's contract, not the plan; separate from satisfied, which answers a different question.
    contract_coverage: str = "complete"
    #: Operator-named units with no trace in the answer, by name.
    unaddressed_units: tuple[str, ...] = ()

    def to_log_payload(self) -> dict[str, Any]:
        return {
            "required": self.required,
            "satisfied": self.satisfied,
            "triggered": self.triggered,
            "requirement_sources": list(self.requirement_sources),
            "contract_coverage": self.contract_coverage,
            "unaddressed_units": list(self.unaddressed_units),
            "missing_requirements": list(self.missing_requirements),
            # Reported every turn: "could not consult this source" differs from "source said nothing required".
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
    """Does the user-facing answer mention this? Checked against the composed answer, not run state."""
    text = (answer or "").casefold()
    if not text:
        return False
    return any(n and n.casefold() in text for n in needles)


#: find_in_files output line: "path:line: text".
_HIT_LINE = re.compile(r"^(\S+?):\d+: ")


def _observed_blob(artifacts: dict[str, Any]) -> str:
    """What the turn actually observed: step labels plus files that passed through them.

    A file given to the lab or hit by a search counts; a name in query text does not.
    """
    # A search label carries the QUERY text, not an observation; only paths of found lines count.
    parts = [str(k) for k, meta in artifacts.items() if (meta or {}).get("tool") != "find_in_files"]
    for meta in artifacts.values():
        meta = meta or {}
        out = meta.get("output")
        if meta.get("tool") == "python_probe" and isinstance(out, dict):
            parts += [str(p) for p in out.get("inputs") or ()]
        elif meta.get("tool") == "find_in_files" and isinstance(out, str):
            lines = out.splitlines()[1:]  # first line is a summary containing the query text
            parts += [m.group(1) if (m := _HIT_LINE.match(ln)) else ln.strip() for ln in lines]
    return " ".join(parts)


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
    contract: Any | None = None,
) -> CompletionObligationResult:
    """Collect obligations from the wired sources and judge each one."""
    artifacts = artifacts or {}
    artifact_tools = {
        str((meta or {}).get("tool") or "") for meta in artifacts.values()
    }
    artifact_blob = _observed_blob(artifacts)
    failure_codes = tuple(str(c) for c in failure_codes if c)
    denied = {str(t).strip() for t in denied_tools if t}

    obligations: list[Obligation] = []

    # ── intent: object-based, a named path or turn-scoped file hint. Read from the requiring part of the
    # request by the same function as core/completion_contract, so a prohibition ("Do not modify X") creates no duty.
    from core.completion_contract import demanding_text
    named = paths_mentioned(demanding_text(question))
    if file_hint and str(file_hint).strip():
        named = tuple(dict.fromkeys((*named, str(file_hint).strip())))
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

    # ── plan: admitted steps only; a step that ran an observing tool owes a result.
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

    # ── freshness: positive source only; realtime_required=False must never cancel other duties.
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

    # ── acceptance_criteria: derived from the request before the work. "Duty met" is judged only against
    # artifacts (the answer is not a parameter of unmet_obligations); "failure disclosed" is read from the answer.
    unavailable = UNWIRED_SOURCES
    if contract is not None:
        from core.completion_contract import unmet_obligations

        unavailable = ()
        unmet = set(unmet_obligations(contract, artifacts=artifacts))
        obligations.extend(
            Obligation(
                source="acceptance_criteria",
                kind="deliverable_produced",
                status=(
                    "satisfied" if duty not in unmet
                    else "failed_but_reported"
                    if failure_codes
                    and _discloses(answer, duty.target or "tests", *failure_codes)
                    else "silently_missing"
                ),
                detail=(
                    f"{duty.deliverable}:{duty.target or 'tests'}"
                    f" — {duty.verification}"
                ),
            )
            for duty in contract.obligations
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
    # Coverage is taken from the contract as is; judging it here would duplicate its owner.
    coverage = str(getattr(contract, "coverage", "complete") or "complete")
    from core.completion_contract import unaddressed_units as _unaddressed
    missing_units = _unaddressed(contract, answer) if contract is not None else ()
    if missing_units:
        notes.append(
            "named units of the request left unaddressed: "
            + "; ".join(missing_units[:5])
        )
    if coverage == "partial":
        notes.append(
            "part of the operator's contract could not be represented; "
            "satisfaction below covers the represented obligations only"
        )

    return CompletionObligationResult(
        required=required,
        satisfied=satisfied,
        triggered=triggered,
        requirement_sources=sources,
        missing_requirements=missing,
        unavailable_sources=unavailable,
        obligations=tuple(obligations),
        contract_coverage=coverage,
        unaddressed_units=missing_units,
        notes=tuple(notes),
    )
