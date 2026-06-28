"""Source ingestion + source-registry + planning REPL commands.

Split out of ``main.py``. The whole cluster is self-contained: it uses only
``cli.parsers``, ``core.ingestion`` / ``core.source_library``, the agent's
public surface, and its own internal helpers — never back into ``main`` — so
there is no import cycle. ``main.py`` re-exports the nine command handlers used
by the REPL dispatch and the conversational router; the payload / format /
extract helpers stay internal to this module.
"""
from __future__ import annotations

import json
import difflib
import re
import sys
from typing import TYPE_CHECKING

from cli.parsers import (
    _parse_ingest_options,
    _parse_source_planning_args,
    _split_meta_args,
)
from core.ingestion import (
    ingest_project,
    ingest_rss_feed,
    ingest_source,
    ingest_web_topic,
)
from core.source_library import list_source_library, source_library_payload

if TYPE_CHECKING:
    from pathlib import Path

    from core.loop import AgentLoop


def _handle_ingest_source(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, _limit, error = _parse_ingest_options(
        rest,
        default_path=None,
    )
    if error or path is None:
        print(error or "Usage: :ingest-source <path>", file=sys.stderr)
        return True
    try:
        report = ingest_source(
            agent=agent,
            workspace=workspace,
            path=path,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_project(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    path, dry_run, auto_write, limit, error = _parse_ingest_options(
        rest,
        default_path=".",
    )
    if error or path is None:
        print(error or "Usage: :ingest-project [path]", file=sys.stderr)
        return True
    try:
        report = ingest_project(
            agent=agent,
            workspace=workspace,
            path=path,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_source_library(rest: str) -> bool:
    tokens = _split_meta_args(rest)
    as_json = False
    group: str | None = None
    for token in tokens:
        if token == "--json":
            as_json = True
            continue
        if group is not None:
            print("Usage: :source-library [group|all] [--json]", file=sys.stderr)
            return True
        group = token

    payload = source_library_payload()
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    entries = list_source_library()
    if group and group != "all":
        wanted = set(payload["groups"].get(group, [group]))
        entries = tuple(entry for entry in entries if entry.id in wanted)
    print("=== source library ===", file=sys.stderr)
    print("groups: " + ", ".join(sorted(payload["groups"])), file=sys.stderr)
    for entry in entries:
        print(
            f"  {entry.id} [{entry.category}] trust={entry.trust_level:.2f} "
            f"domains={','.join(entry.allowed_domains)}",
            file=sys.stderr,
        )
        print(f"    {entry.description}", file=sys.stderr)
    return True


def _handle_source_registry(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    as_json = False
    limit = 20
    show_claims = False
    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--json":
            as_json = True
            i += 1
            continue
        if token == "--claims":
            show_claims = True
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        print(f"Usage: unknown :source-registry option {token}", file=sys.stderr)
        return True
    if limit < 1:
        print("Usage: --limit must be >= 1", file=sys.stderr)
        return True

    store = getattr(agent, "source_registry_store", None)
    registry = store.load_registry() if store is not None else getattr(agent, "last_source_registry", None)
    if registry is None:
        payload = {
            "path": None,
            "sources": 0,
            "claims": 0,
            "items": [],
        }
    else:
        claims_by_source: dict[str, list] = {}
        for claim in registry.claims:
            claims_by_source.setdefault(claim.source_id, []).append(claim)
        sources = list(registry.sources)
        items = []
        for source in sources[:limit]:
            source_claims = claims_by_source.get(source.id, [])
            item = {
                "id": source.id,
                "type": source.type,
                "title": source.title,
                "locator": source.locator,
                "trust_level": source.trust_level,
                "claim_count": len(source_claims),
            }
            if show_claims:
                item["claims"] = [
                    {
                        "id": claim.id,
                        "status": claim.status,
                        "confidence": claim.confidence,
                        "locator": claim.locator,
                        "text": claim.text,
                    }
                    for claim in source_claims[:limit]
                ]
            items.append(item)
        payload = {
            "path": str(store.path) if store is not None else None,
            "sources": len(registry.sources),
            "claims": len(registry.claims),
            "shown": len(items),
            "limit": limit,
            "items": items,
        }

    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    print("=== source registry ===", file=sys.stderr)
    if payload["path"]:
        print(f"path: {payload['path']}", file=sys.stderr)
    print(
        f"sources={payload['sources']} claims={payload['claims']} "
        f"shown={payload.get('shown', 0)} limit={payload.get('limit', limit)}",
        file=sys.stderr,
    )
    if not payload["items"]:
        print("(no ingested sources)", file=sys.stderr)
        return True
    for item in payload["items"]:
        print(
            f"  {item['id']} [{item['type']}] claims={item['claim_count']} "
            f"trust={float(item['trust_level']):.2f}",
            file=sys.stderr,
        )
        title = item.get("title") or item.get("locator") or ""
        if title:
            print(f"    {title}", file=sys.stderr)
        if show_claims:
            for claim in item.get("claims", []):
                print(
                    f"    - [{claim['status']} {float(claim['confidence']):.2f}] "
                    f"{claim['text']}",
                    file=sys.stderr,
                )
    return True


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
    agent.log.log("operator_source_review_plan", payload)
    if as_json:
        print(json.dumps(payload, ensure_ascii=False, indent=2), file=sys.stderr)
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


def _handle_self_build_propose(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del rest, agent
    payload = _self_build_propose_payload(workspace)
    patch = payload["diff"]
    if patch == "NO_PATCH":
        print("NO_PATCH", file=sys.stderr)
        return True
    lines = [
        "=== self-build proposal ===",
        f"diagnosis: {payload['diagnosis']}",
        f"file: {payload['file']}",
        "diff:",
        patch.rstrip(),
        f"tests: {payload['tests']}",
        f"risk: {payload['risk']}",
    ]
    print("\n".join(lines), file=sys.stderr)
    return True


def _self_build_propose_payload(workspace: Path) -> dict[str, str]:
    target = "core/operator_intent.py"
    path = workspace / target
    if not path.is_file():
        return _self_build_no_patch(target)
    original = path.read_text(encoding="utf-8")
    patched = _propose_self_build_operator_intent_patch(original)
    if patched is None or patched == original:
        return _self_build_no_patch(target)
    diff = "".join(
        difflib.unified_diff(
            original.splitlines(keepends=True),
            patched.splitlines(keepends=True),
            fromfile=target,
            tofile=target,
        )
    )
    if not diff.startswith("--- ") or "\n@@ " not in diff:
        return _self_build_no_patch(target)
    return {
        "diagnosis": (
            "Self-build prompts can be captured by generic operator shortcuts "
            "before the planner sees them."
        ),
        "file": target,
        "diff": diff,
        "tests": r".\.venv\Scripts\python.exe -m pytest tests/test_operator_intent.py tests/test_cli.py -q",
        "risk": "low; explicit ':' commands are dispatched before conversational routing.",
    }


def _self_build_no_patch(target: str) -> dict[str, str]:
    return {
        "diagnosis": "No ready unified diff is available for the self-build routing guard.",
        "file": target,
        "diff": "NO_PATCH",
        "tests": r".\.venv\Scripts\python.exe -m pytest tests/test_operator_intent.py tests/test_cli.py -q",
        "risk": "none; no patch is proposed.",
    }


def _propose_self_build_operator_intent_patch(text: str) -> str | None:
    patched = text
    guard = (
        "    if _looks_like_meta_instruction(normalized):\n"
        "        return None\n"
    )
    if "_looks_like_self_build_request(normalized)" not in patched:
        if guard not in patched:
            return None
        patched = patched.replace(
            guard,
            guard
            + "    if _looks_like_self_build_request(normalized):\n"
            + "        return None\n",
            1,
        )
    if "def _looks_like_self_build_request(" not in patched:
        helper_anchor = "\n\ndef _matches_inbox_task_request"
        helper = (
            "\n\n"
            "def _looks_like_self_build_request(text: str) -> bool:\n"
            "    return _has_any(\n"
            "        text,\n"
            '        ("self-build", "selfbuild", "self build", "самостро"),\n'
            "    ) and _has_any(\n"
            "        text,\n"
            '        ("propose", "inspect", "найди", "проанализ", "улучш", "код", "code", "diff"),\n'
            "    )\n"
        )
        if helper_anchor not in patched:
            return None
        patched = patched.replace(helper_anchor, helper + helper_anchor, 1)
    return patched


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
    implementation_steps = _implementation_steps_for_kind(kind)
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
        "tests_to_add": _implementation_tests_for_kind(kind),
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


def _implementation_steps_for_kind(kind: str) -> list[str]:
    if kind == "patch_proposal":
        return [
            "Collect explicit file evidence for every target file before proposing a diff.",
            "Identify the smallest behavioral mismatch and map it to one code surface.",
            "Draft a minimal patch proposal with expected before/after behavior.",
            "Name targeted tests and rollback expectations, then stop before applying.",
        ]
    return [
        "Separate source evidence from implementation assumptions.",
        "Map each requested behavior to the smallest likely file/function surface.",
        "Add or adjust deterministic routing/handler code before any broad refactor.",
        "Add positive routing tests and negative over-routing tests.",
        "Run targeted tests first, then full pytest before commit.",
    ]


def _implementation_tests_for_kind(kind: str) -> list[str]:
    tests = [
        "source-review requests route to source_review_plan, not project_health",
        "implementation-plan requests route to implementation_plan, not source_review_plan",
        "handler returns a local report without planner/LLM calls",
    ]
    if kind == "patch_proposal":
        tests.append("patch proposal requests return a patch proposal plan, not a generic source review")
    else:
        tests.append("implementation plan report lists files/functions, tests, risks and approval boundary")
    return tests


def _format_implementation_plan(payload: dict) -> str:
    kind = payload.get("kind")
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
    if out.startswith("file:"):
        out = out[len("file:"):]
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
    names = {_normalize_source_key(item) for item in mentions}
    for source in sources:
        for value in (getattr(source, "id", ""), getattr(source, "locator", ""), getattr(source, "title", "")):
            key = _normalize_source_key(str(value))
            if key:
                names.add(key)
    suggestions: list[str] = []
    if any("core\\operator_intent.py" in name or "operator_intent.py" == name for name in names):
        suggestions.append("core/operator_intent.py - route source-review and implementation-plan language")
    if any("main.py" in name for name in names):
        suggestions.append("main.py - dispatch the operator source-review plan command")
    if any("test" in name for name in names) or suggestions:
        suggestions.append("tests/test_operator_intent.py - routing regressions")
        suggestions.append("tests/test_cli.py - command/handler regressions")
    if not suggestions:
        suggestions.append("Use explicit multi-file review mode or :ingest-source to inspect the relevant files before patching.")
    return suggestions


def _handle_ingest_web(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 5
    per_source = 1
    source_selection: str | None = None
    topic_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--sources":
            if i + 1 >= len(tokens):
                print("Usage: --sources requires a comma-separated list or group", file=sys.stderr)
                return True
            source_selection = tokens[i + 1]
            i += 2
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        if token == "--per-source":
            if i + 1 >= len(tokens):
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            try:
                per_source = int(tokens[i + 1])
            except ValueError:
                print("Usage: --per-source requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        topic_parts.append(token)
        i += 1

    topic = " ".join(topic_parts).strip()
    if not topic:
        print(
            "Usage: :ingest-web <topic> [--sources wikis|books|science|docs|all|id,id] "
            "[--limit N] [--per-source N] [--dry-run] [--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_web_topic(
            agent=agent,
            topic=topic,
            source_selection=source_selection,
            limit=limit,
            per_source=per_source,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest web failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True


def _handle_ingest_rss(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    del workspace
    tokens = _split_meta_args(rest)
    dry_run = False
    auto_write: bool | None = None
    limit = 10
    url_parts: list[str] = []

    i = 0
    while i < len(tokens):
        token = tokens[i]
        if token == "--dry-run":
            dry_run = True
            i += 1
            continue
        if token == "--write-memory":
            auto_write = True
            i += 1
            continue
        if token == "--no-memory":
            auto_write = False
            i += 1
            continue
        if token == "--limit":
            if i + 1 >= len(tokens):
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            try:
                limit = int(tokens[i + 1])
            except ValueError:
                print("Usage: --limit requires a number", file=sys.stderr)
                return True
            i += 2
            continue
        url_parts.append(token)
        i += 1

    url = " ".join(url_parts).strip()
    if not url:
        print(
            "Usage: :ingest-rss <feed_url> [--limit N] [--dry-run] "
            "[--write-memory|--no-memory]",
            file=sys.stderr,
        )
        return True

    try:
        report = ingest_rss_feed(
            agent=agent,
            url=url,
            limit=limit,
            dry_run=dry_run,
            auto_write_memory=auto_write,
        )
    except Exception as exc:
        print(f"(ingest rss failed: {type(exc).__name__}: {exc})", file=sys.stderr)
        return True
    print(report.user_summary(), file=sys.stderr)
    return True
