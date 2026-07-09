"""``:self-split <path>`` REPL command (junior-plan item #5).

Plans ONE deterministic incremental extraction step for an oversized Python
module (:mod:`core.incremental_splitter`) and publishes it as a normal
``self_apply_lane.run`` approval item. It never applies anything itself: the
human approves the item and runs it via ``:self-apply-run``, which executes
targeted + full tests and auto-rolls back on red.

Unlike ``:self-build-produce`` this path uses NO LLM at all -- the code is
moved verbatim by AST line slicing -- so it has no output-token ceiling and is
allowed to work on modules far beyond the one-shot split limit (e.g.
``core/loop.py``). Because no model generates content, the deterministic
planner refuses any step it cannot prove safe (both post-images parse, the
target shrinks, every moved top-level name stays importable).
"""
from __future__ import annotations

import hashlib
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from core.incremental_splitter import plan_incremental_split
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload

from cli.commands_approval import _approval_inbox_for

if TYPE_CHECKING:
    from core.loop import AgentLoop

SELF_SPLIT_ORIGIN = "incremental_splitter"


def _handle_self_split(rest: str, agent: "AgentLoop", workspace: Path) -> bool:
    parts = rest.split()
    if len(parts) != 1:
        print(
            "Usage: :self-split <relative/path.py>\n"
            "  (plans one deterministic extraction step and creates one "
            "approval item)",
            file=sys.stderr,
        )
        return True
    target = parts[0].replace("\\", "/")

    plan = plan_incremental_split(workspace, target)

    agent.log.log(
        "self_split_plan",
        {
            "target": target,
            "status": plan.status,
            "reason": plan.reason,
            "mode": plan.step.mode if plan.step else None,
            "new_module": plan.step.new_module if plan.step else None,
            "moved_names": plan.step.moved_names if plan.step else [],
            "lines_moved": plan.step.lines_moved if plan.step else 0,
        },
    )

    if plan.status != "planned" or plan.step is None:
        print(
            "=== self-split ===\n"
            f"status: {plan.status}\n"
            f"reason: {plan.reason}\n"
            "next: Pick a different target or raise no limits -- the planner "
            "only proposes steps it can prove safe.",
            file=sys.stderr,
        )
        return True

    step = plan.step
    build = {
        "files": [
            {"path": step.target, "content": step.target_content},
            {"path": step.new_module, "content": step.new_content},
        ],
    }
    # Keep docs/AGENT_ANATOMY.md in sync (its drift check would fail otherwise).
    try:
        from core.self_build_producer import _default_file_reader, _sync_anatomy_index

        _sync_anatomy_index(build, step.target, _default_file_reader(workspace))
    except Exception:  # noqa: BLE001 -- doc sync is best-effort; lane catches drift
        pass

    # Targeted tests: importer tests from the dependency map, plus the full
    # suite fallback the lane always accepts.
    test_paths: list[str] = ["tests"]
    try:
        from core.dependency_map import build_dependency_map

        related = build_dependency_map(workspace, step.target).related_tests
        if related:
            test_paths = sorted(related)
    except Exception:  # noqa: BLE001 -- dep scan is advisory only
        pass

    evidence = [
        f"mode={step.mode}",
        f"target={step.target}",
        f"new_module={step.new_module}",
        f"lines_moved={step.lines_moved}",
        f"moved_names={', '.join(step.moved_names)}",
        "generator=deterministic AST slice (no LLM)",
        *step.notes,
    ]
    payload = build_self_apply_payload(
        files=build["files"],
        reason=plan.reason,
        evidence=evidence,
        test_paths=test_paths,
        origin=SELF_SPLIT_ORIGIN,
    )
    digest = hashlib.sha1(step.target_content.encode("utf-8")).hexdigest()[:12]
    inbox = _approval_inbox_for(agent, workspace)
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=(
            f"incremental split step for {step.target}: move "
            f"{len(step.moved_names)} name(s) into {step.new_module}"
        ),
        risk="reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=f"self_split:{step.target}:{digest}",
    )

    print(
        "=== self-split ===\n"
        "status: proposed\n"
        f"reason: {plan.reason}\n"
        f"mode: {step.mode}\n"
        f"target: {step.target}\n"
        f"new_module: {step.new_module}\n"
        f"moved: {', '.join(step.moved_names)}\n"
        f"approval_id: {item.id}\n"
        f"next: Review, then :approval-approve {item.id} and "
        f":self-apply-run {item.id}. Repeat :self-split {step.target} for the "
        "next step.",
        file=sys.stderr,
    )
    return True
