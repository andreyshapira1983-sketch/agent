"""Deterministic REPL planners: `:source-review-plan`,
`:implementation-plan`, `:patch-proposal-plan`.

Split out of ``cli/commands_ingest.py`` on 2026-08-20: the cluster scan found
these 25 members shared no reference with the ingest handlers they lived
beside — a module inside a module. They read the Source Registry and emit a
plan payload; they never ingest, never write, and never call a model.
"""
from __future__ import annotations

import json
import re
import sys
from typing import TYPE_CHECKING

from cli.parsers import _parse_source_planning_args

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop

def _handle_source_review_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":source-review-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _source_review_plan_payload(goal, agent, limit=limit)
    blocked = _insufficient_source_evidence_payload(
        goal, payload, requested_kind="source_review_plan"
    )
    if blocked is not None:
        payload = blocked
    agent.log.log("operator_source_review_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    elif payload.get("kind") == "insufficient_source_evidence":
        print(_format_insufficient_source_evidence(payload), file=sys.stderr)
    else:
        print(_format_source_review_plan(payload), file=sys.stderr)
    return True


def _handle_implementation_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":implementation-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _implementation_plan_payload(
        goal,
        agent,
        limit=limit,
        kind="implementation_plan",
    )
    agent.log.log("operator_implementation_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_implementation_plan(payload), file=sys.stderr)
    return True


def _handle_patch_proposal_plan(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    parsed = _parse_source_planning_args(
        rest,
        usage=":patch-proposal-plan <goal> [--limit N] [--json]",
    )
    if parsed is None:
        return True
    as_json, limit, goal = parsed
    payload = _implementation_plan_payload(
        goal,
        agent,
        limit=limit,
        kind="patch_proposal",
    )
    agent.log.log("operator_patch_proposal_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
    else:
        print(_format_implementation_plan(payload), file=sys.stderr)
    return True


# Verbs + qualifiers that mark an *explicit* "read/inspect/review these files
# first" request. When such a request names files but none of them are grounded
# in the Source Registry, the deterministic planners must NOT emit a plan that
# pretends to be source-grounded (sources=0). They return a blocked
# insufficient_source_evidence result instead (read-only; no web/tests/writes).
_EXPLICIT_READ_VERBS = (
    "read",
    "inspect",
    "review",
    "open",
    "прочит",
    "прочти",
    "изуч",
    "просмотр",
    "ознаком",
)
_EXPLICIT_READ_QUALIFIERS = (
    "first",
    "before",
    "only these files",
    "only the files",
    "only these",
    "these files first",
    "read these files",
    "сначала",
    "перед тем",
    "перед этим",
    "только эти файл",
    "лишь эти файл",
)


def _requests_explicit_file_read(text: str) -> bool:
    """True when the operator explicitly asks to read/inspect named files first."""
    lowered = " ".join(str(text or "").casefold().split())
    if not lowered:
        return False
    has_verb = any(verb in lowered for verb in _EXPLICIT_READ_VERBS)
    has_qualifier = any(q in lowered for q in _EXPLICIT_READ_QUALIFIERS)
    return has_verb and has_qualifier


def _insufficient_source_evidence_payload(
    goal: str,
    source_review: dict,
    *,
    requested_kind: str,
) -> dict | None:
    """Return a blocked payload when an explicit read-first request is ungrounded.

    Trigger: the goal explicitly asks to read/inspect/review named files first,
    those files are named (``requested_mentions``), but none of them were matched
    in the Source Registry (``matched_sources`` empty). Otherwise returns None so
    the normal deterministic plan is produced.
    """
    if not _requests_explicit_file_read(goal):
        return None
    requested = list(source_review.get("requested_mentions", []))
    if not requested:
        return None
    if source_review.get("matched_sources"):
        return None
    missing = list(source_review.get("missing_mentions", [])) or requested
    return {
        "kind": "insufficient_source_evidence",
        "requested_kind": requested_kind,
        "status": "blocked",
        "goal": goal or "insufficient source evidence",
        "reason": (
            "Explicit request to read/inspect/review the named files first, but "
            "none of them were read or verified (sources=0). Refusing to emit a "
            "grounded plan from unread files."
        ),
        "source_evidence": {
            "registry": source_review.get("registry", {}),
            "requested_mentions": requested,
            "matched_sources": [],
            "missing_mentions": missing,
            "matched_source_ids": [],
        },
        "files_not_read": missing,
        "next_actions": [
            ("Read the named files first: re-ask without a deterministic planning "
            "shortcut so the read-capable planner opens them, or use explicit "
            "multi-file review mode."),
            ("Or ingest them (:ingest-source / :ingest-project) so the Source "
            "Registry has verifiable claims, then re-run the plan."),
        ],
        "constraints": [
            "read-only planning guard",
            "no web search",
            "no tests",
            "no file writes",
            "no shell execution",
            "no autonomous allow-effects",
        ],
    }


def _format_insufficient_source_evidence(payload: dict) -> str:
    evidence = payload.get("source_evidence", {})
    registry = evidence.get("registry", {})
    lines = [
        "=== insufficient source evidence (blocked) ===",
        f"status: {payload.get('status', 'blocked')}",
        f"requested kind: {payload.get('requested_kind')}",
        f"goal: {payload.get('goal')}",
        f"reason: {payload.get('reason')}",
        (
            "source evidence: "
            f"sources={registry.get('sources', 0)} "
            f"claims={registry.get('claims', 0)} "
            f"matched={len(evidence.get('matched_sources', []))} "
            f"missing={len(evidence.get('missing_mentions', []))}"
        ),
        "requested files NOT read/verified:",
    ]
    files_not_read = payload.get("files_not_read", [])
    lines.extend(f"  - {item}" for item in (files_not_read or ["(none captured)"]))
    lines.append("next actions:")
    lines.extend(f"  - {item}" for item in payload.get("next_actions", []))
    lines.append("constraints:")
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    return "\n".join(lines)


def _source_review_plan_payload(goal: str, agent: AgentLoop, *, limit: int = 8) -> dict:
    store = getattr(agent, "source_registry_store", None)
    registry = store.load_registry() if store is not None else getattr(agent, "last_source_registry", None)
    if registry is None:
        sources = []
        claims = []
    else:
        sources = list(registry.sources)
        claims = list(registry.claims)
    claims_by_source: dict[str, list] = {}
    for claim in claims:
        claims_by_source.setdefault(claim.source_id, []).append(claim)

    mentions = _extract_source_review_mentions(goal)
    matched_sources = _match_sources_to_mentions(sources, mentions)
    if not mentions and sources:
        matched_sources = sources[:limit]
    if mentions:
        matched_ids = {source.id for source in matched_sources}
        missing_mentions = [
            mention for mention in mentions
            if not any(_mention_matches_source(mention, source) for source in matched_sources)
        ]
    else:
        matched_ids = {source.id for source in matched_sources}
        missing_mentions = []

    items = []
    for source in matched_sources[:limit]:
        source_claims = claims_by_source.get(source.id, [])
        items.append({
            "id": source.id,
            "type": source.type,
            "title": source.title,
            "locator": source.locator,
            "claim_count": len(source_claims),
            "sample_claims": [
                {
                    "status": claim.status,
                    "confidence": claim.confidence,
                    "text": claim.text,
                }
                for claim in source_claims[:2]
            ],
        })

    suggested_files = _suggest_implementation_files(matched_sources, mentions)
    return {
        "goal": goal or "source review implementation plan",
        "registry": {
            "path": str(store.path) if store is not None else None,
            "sources": len(sources),
            "claims": len(claims),
        },
        "requested_mentions": mentions,
        "matched_sources": items,
        "missing_mentions": missing_mentions,
        "suggested_files": suggested_files,
        "plan": [
            "Use only matched Source Registry entries as current evidence; inspect missing files explicitly before claiming they were reviewed.",
            "Confirm the intended behavior from the task/source claims, then map each change to one small code surface.",
            "Patch routing/handler code first, then add regression tests for positive routing and negative over-routing.",
            "Run targeted tests for operator intent/CLI, then the full pytest suite before commit.",
        ],
        "tests_to_add": [
            "source-review planning request routes to source_review_plan, not project_health",
            "source review plan lists matched ingested sources and missing requested sources",
            "handler does not call planner/LLM for this operator digest",
            "existing project health and next-actions phrases still route correctly",
        ],
        "constraints": [
            "read-only planning digest",
            "no file writes",
            "no shell execution",
            "no autonomous allow-effects",
            "no persistent memory promotion",
        ],
        "matched_source_ids": sorted(matched_ids),
    }


def _implementation_plan_payload(
    goal: str,
    agent: AgentLoop,
    *,
    limit: int = 8,
    kind: str = "implementation_plan",
) -> dict:
    source_review = _source_review_plan_payload(goal, agent, limit=limit)
    blocked = _insufficient_source_evidence_payload(
        goal, source_review, requested_kind=kind
    )
    if blocked is not None:
        return blocked
    if not source_review.get("requested_mentions"):
        source_review = {
            **source_review,
            "matched_sources": [],
            "matched_source_ids": [],
            "suggested_files": [
                "Use explicit multi-file review mode or :ingest-source to inspect the relevant files before patching."
            ],
        }
    suggested_files = source_review.get("suggested_files", [])
    implementation_steps = _implementation_steps_for_kind(kind, goal, source_review)
    return {
        "kind": kind,
        "goal": goal or ("patch proposal plan" if kind == "patch_proposal" else "implementation plan"),
        "source_evidence": {
            "registry": source_review.get("registry", {}),
            "requested_mentions": source_review.get("requested_mentions", []),
            "matched_sources": source_review.get("matched_sources", []),
            "missing_mentions": source_review.get("missing_mentions", []),
            "matched_source_ids": source_review.get("matched_source_ids", []),
        },
        "files_functions_to_inspect_or_change": suggested_files,
        "implementation_steps": implementation_steps,
        "tests_to_add": _implementation_tests_for_kind(kind, goal, source_review),
        "approval_boundary": [
            "This report is read-only planning.",
            "Do not edit files, run shell commands, apply repair, or enable allow-effects from this step.",
            "Require explicit approval before any file_write, shell_exec, repair apply, rollback, or persistent memory promotion.",
        ],
        "risks": [
            "Missing source mentions are not verified and must be read explicitly before patching.",
            "A plan based only on registry claims may be stale if code changed after ingestion.",
            "Routing changes are high leverage; keep positive and negative regression tests close to the change.",
        ],
        "constraints": [
            "local deterministic operator report",
            "no LLM required",
            "no web",
            "no file writes",
            "no shell execution",
            "no autonomous allow-effects",
        ],
    }


def _implementation_steps_for_kind(
    kind: str,
    goal: str = "",
    source_review: dict | None = None,
) -> list[str]:
    if kind == "patch_proposal":
        target_paths = _source_review_target_paths(source_review)
        target_text = _format_target_list(target_paths) if target_paths else "the requested files"
        behavior = _goal_behavior_label(goal)
        return [
            f"Use matched Source Registry evidence for {target_text} before proposing a diff.",
            f"Map the requested behavior ({behavior}) to the smallest code surface.",
            "Draft a minimal patch proposal with expected before/after behavior.",
            f"Name behavior-specific tests for {behavior}, then stop before applying.",
        ]
    return [
        "Separate source evidence from implementation assumptions.",
        "Map each requested behavior to the smallest likely file/function surface.",
        "Add or adjust deterministic routing/handler code before any broad refactor.",
        "Add positive routing tests and negative over-routing tests.",
        "Run targeted tests first, then full pytest before commit.",
    ]


def _implementation_tests_for_kind(
    kind: str,
    goal: str = "",
    source_review: dict | None = None,
) -> list[str]:
    if kind == "patch_proposal":
        return _patch_proposal_tests(goal, source_review)
    tests = [
        "source-review requests route to source_review_plan, not project_health",
        "implementation-plan requests route to implementation_plan, not source_review_plan",
        "handler returns a local report without planner/LLM calls",
    ]
    tests.append("implementation plan report lists files/functions, tests, risks and approval boundary")
    return tests


def _patch_proposal_tests(goal: str, source_review: dict | None) -> list[str]:
    behavior = _goal_behavior_label(goal)
    target_paths = _source_review_target_paths(source_review)
    if target_paths:
        tests = [
            f"{path}: regression covers requested behavior ({behavior})"
            for path in target_paths[:3]
        ]
    else:
        tests = [f"regression covers requested patch behavior ({behavior})"]
    tests.append(
        "patch proposal command reports matched requested files and stays read-only without planner/LLM calls"
    )
    return tests


def _source_review_target_paths(source_review: dict | None) -> list[str]:
    if not source_review:
        return []
    return _target_paths_from_sources_and_mentions(
        list(source_review.get("matched_sources", [])),
        list(source_review.get("requested_mentions", [])),
    )


def _target_paths_from_sources_and_mentions(sources: list, mentions: list[str]) -> list[str]:
    paths: list[str] = []
    seen: set[str] = set()

    def add(value: str) -> None:
        path = _display_source_path(value)
        if path is None:
            return
        key = path.casefold()
        if key in seen:
            return
        seen.add(key)
        paths.append(path)

    for mention in mentions:
        add(mention)
    for source in sources:
        for field in ("locator", "id", "title"):
            value = _source_field(source, field)
            if not value:
                continue
            before = len(paths)
            add(value)
            if len(paths) > before:
                break
    return paths


def _source_field(source, field: str) -> str:
    if isinstance(source, dict):
        return str(source.get(field, ""))
    return str(getattr(source, field, ""))


def _display_source_path(value: str) -> str | None:
    out = value.strip().strip("\"'")
    out = out.removeprefix("file:")
    out = out.replace("\\", "/")
    while out.startswith("./"):
        out = out[2:]
    out = out.rstrip(".,;:!?)]}\"'")
    if not re.search(r"\.(?:py|md|txt|json|yml|yaml|pdf)$", out, flags=re.IGNORECASE):
        return None
    return out


def _format_target_list(paths: list[str]) -> str:
    if len(paths) <= 3:
        return ", ".join(paths)
    return ", ".join(paths[:3]) + f", and {len(paths) - 3} more"


def _goal_behavior_label(goal: str) -> str:
    compact = " ".join(goal.split()).strip()
    if not compact:
        return "requested patch behavior"
    if len(compact) > 120:
        compact = compact[:117].rstrip() + "..."
    return compact


def _format_implementation_plan(payload: dict) -> str:
    kind = payload.get("kind")
    if kind == "insufficient_source_evidence":
        return _format_insufficient_source_evidence(payload)
    title = "patch proposal plan" if kind == "patch_proposal" else "implementation plan"
    evidence = payload.get("source_evidence", {})
    registry = evidence.get("registry", {})
    lines = [
        f"=== {title} ===",
        f"kind: {kind}",
        f"goal: {payload.get('goal')}",
        (
            "source evidence: "
            f"sources={registry.get('sources', 0)} "
            f"claims={registry.get('claims', 0)} "
            f"matched={len(evidence.get('matched_sources', []))} "
            f"missing={len(evidence.get('missing_mentions', []))}"
        ),
    ]
    requested = evidence.get("requested_mentions", [])
    if requested:
        lines.append("requested sources/files:")
        lines.extend(f"  - {item}" for item in requested)
    matched = evidence.get("matched_sources", [])
    if matched:
        lines.append("matched evidence:")
        for item in matched:
            lines.append(
                f"  - {item.get('id')} [{item.get('type')}] claims={item.get('claim_count', 0)}"
            )
    missing = evidence.get("missing_mentions", [])
    if missing:
        lines.append("not verified from registry:")
        lines.extend(f"  - {item}" for item in missing)
    lines.append("files/functions to inspect or change:")
    lines.extend(f"  - {item}" for item in payload.get("files_functions_to_inspect_or_change", []))
    lines.append("implementation steps:")
    lines.extend(
        f"  {idx}. {item}"
        for idx, item in enumerate(payload.get("implementation_steps", []), start=1)
    )
    lines.append("tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("risks:")
    lines.extend(f"  - {item}" for item in payload.get("risks", []))
    lines.append("approval boundary:")
    lines.extend(f"  - {item}" for item in payload.get("approval_boundary", []))
    lines.append("constraints:")
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    return "\n".join(lines)


def _format_source_review_plan(payload: dict) -> str:
    registry = payload.get("registry", {})
    lines = [
        "=== source review plan ===",
        f"goal: {payload.get('goal')}",
        (
            "registry: "
            f"sources={registry.get('sources', 0)} "
            f"claims={registry.get('claims', 0)}"
        ),
    ]
    mentions = payload.get("requested_mentions", [])
    if mentions:
        lines.append("requested sources:")
        lines.extend(f"  - {item}" for item in mentions)
    matched = payload.get("matched_sources", [])
    if matched:
        lines.append("matched ingested sources:")
        for item in matched:
            lines.append(
                f"  - {item.get('id')} [{item.get('type')}] "
                f"claims={item.get('claim_count', 0)}"
            )
            for claim in item.get("sample_claims", []):
                lines.append(
                    f"    claim[{claim.get('status')} {float(claim.get('confidence', 0)):.2f}]: "
                    f"{claim.get('text')}"
                )
    else:
        lines.append("matched ingested sources: none")
    missing = payload.get("missing_mentions", [])
    if missing:
        lines.append("not verified from registry:")
        lines.extend(f"  - {item}" for item in missing)
    suggested = payload.get("suggested_files", [])
    if suggested:
        lines.append("likely files to inspect/change:")
        lines.extend(f"  - {item}" for item in suggested)
    lines.append("implementation plan:")
    lines.extend(f"  {idx}. {step}" for idx, step in enumerate(payload.get("plan", []), start=1))
    lines.append("tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("constraints:")
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    return "\n".join(lines)


def _extract_source_review_mentions(text: str) -> list[str]:
    pattern = re.compile(
        r"(?P<path>"
        r"(?:[A-Za-z]:[\\/])?"
        r"(?:\.{1,2}[\\/])?"
        r"(?:[A-Za-z0-9_.-]+[\\/])*"
        r"[A-Za-z0-9_.-]+\."
        r"(?:py|md|txt|json|yml|yaml|pdf)"
        r")",
        flags=re.IGNORECASE,
    )
    seen: set[str] = set()
    mentions: list[str] = []
    for match in pattern.finditer(text):
        value = match.group("path").rstrip(".,;:!?)\"]}'")
        key = _normalize_source_key(value)
        if key in seen:
            continue
        seen.add(key)
        mentions.append(value)
    return mentions


def _match_sources_to_mentions(sources: list, mentions: list[str]) -> list:
    if not mentions:
        return []
    matched = []
    seen: set[str] = set()
    for source in sources:
        if any(_mention_matches_source(mention, source) for mention in mentions):
            key = _source_dedupe_key(source)
            if key not in seen:
                matched.append(source)
                seen.add(key)
    return matched


def _mention_matches_source(mention: str, source) -> bool:
    mention_key = _normalize_source_key(mention)
    candidates = [
        getattr(source, "id", ""),
        getattr(source, "title", ""),
        getattr(source, "locator", ""),
    ]
    for candidate in candidates:
        key = _normalize_source_key(str(candidate))
        if mention_key == key or mention_key in key or key.endswith(mention_key):
            return True
    return False


def _normalize_source_key(value: str) -> str:
    out = value.strip().strip("\"'")
    out = out.removeprefix("file:")
    out = out.replace("/", "\\")
    while out.startswith(".\\"):
        out = out[2:]
    return out.casefold()


def _source_dedupe_key(source) -> str:
    for value in (
        getattr(source, "locator", ""),
        getattr(source, "title", ""),
        getattr(source, "id", ""),
    ):
        key = _normalize_source_key(str(value))
        if key:
            return key
    return str(getattr(source, "id", "")).casefold()


def _suggest_implementation_files(sources: list, mentions: list[str]) -> list[str]:
    target_paths = _target_paths_from_sources_and_mentions(sources, mentions)
    suggestions: list[str] = []
    for path in target_paths:
        lowered = path.casefold()
        if lowered.endswith("core/operator_intent.py"):
            suggestions.append(f"{path} - route requested operator intent behavior")
        elif lowered == "main.py":
            suggestions.append(f"{path} - dispatch/format the requested operator command behavior")
        elif lowered.startswith("tests/") or "/test_" in lowered:
            suggestions.append(f"{path} - add behavior-specific regression coverage")
        elif lowered.endswith(".py"):
            suggestions.append(f"{path} - inspect/change requested behavior")
        else:
            suggestions.append(f"{path} - review requested source evidence")
    if not suggestions:
        suggestions.append("Use explicit multi-file review mode or :ingest-source to inspect the relevant files before patching.")
    return suggestions
