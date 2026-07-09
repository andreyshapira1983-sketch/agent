"""``:operator-task`` read-only planning/report handler.

Split out of ``main.py``. Classifies operator tasks (safe system check vs
programming/patch proposal), gathers digest evidence, and prints structured
reports. Does not run file_write, shell_exec, repair, or allow-effects.

``main.py`` re-exports ``_handle_operator_task`` for dispatch.
"""
from __future__ import annotations

import sys
from pathlib import Path

from app.operator_status import _operator_digest_payload
from cli.commands_budget import (
    _autonomy_readiness_payload,
    _persistent_budget_limits_configured,
)
from cli.parsers import _compact_one_line
from core.loop import AgentLoop


def _handle_operator_task(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    block = rest.strip()
    if not block:
        print("Usage: :operator-task <task text> or start a block and finish with :end", file=sys.stderr)
        return True
    payload = _operator_task_payload(block, agent, workspace)
    agent.log.log("operator_task_report", payload)
    print(_format_operator_task_report(payload), file=sys.stderr)
    return True


def _operator_task_payload(block: str, agent: AgentLoop, workspace: Path) -> dict:
    text = block.casefold()
    if _looks_like_operator_programming_task(text):
        return _operator_programming_task_payload(block, agent, workspace)

    digest = _operator_digest_payload(agent, workspace)
    readiness = _autonomy_readiness_payload(digest)
    requested_checks = _extract_operator_task_checks(text)
    constraints = _extract_operator_task_constraints(text)
    if not constraints:
        constraints = [
            "no file/code changes",
            "no external side effects",
            "no repair",
            "no allow-effects",
            "no subagents",
            "no automatic persistent memory writes",
        ]
    architecture = digest.get("architecture", {})
    runtime = digest.get("runtime", {})
    queue = digest.get("task_queue", {})
    scheduler = digest.get("scheduler", {})
    model_usage = digest.get("model_usage") or {}
    windows = digest.get("persistent_budget_windows")
    approvals = runtime.get("approval_inbox", {})
    source_registry = runtime.get("source_registry", {})
    working_normally = [
        f"architecture audit loaded with status_counts={architecture.get('status_counts', {})}",
        (
            "source registry available: "
            f"sources={source_registry.get('sources', 0)} claims={source_registry.get('claims', 0)}"
        ),
        (
            "approvals queue visible: "
            f"pending={approvals.get('pending', 0)} total={approvals.get('total', 0)}"
        ),
        (
            "task queue/scheduler visible: "
            f"pending_due={queue.get('pending_due', 0)} scheduler_due={scheduler.get('due', 0)}"
        ),
    ]
    requires_attention = list(digest.get("recommendations", []))
    if not _persistent_budget_limits_configured(windows):
        requires_attention.append(
            "Persistent hour/day budget limits are not configured; long unattended work should wait."
        )
    dangerous_now = list(readiness.get("blockers", []))
    if not dangerous_now:
        dangerous_now.append("No immediate blocker for dry-run operator checks; keep effects disabled.")
    next_step = _operator_task_next_step(requires_attention, dangerous_now)
    return {
        "goal": "safe_system_check",
        "raw_task": block,
        "checks": requested_checks,
        "constraints": constraints,
        "output_contract": [
            "what works normally",
            "what requires attention",
            "what is dangerous to run now",
            "one safest next step",
        ],
        "what_works_normally": working_normally,
        "what_requires_attention": requires_attention,
        "what_is_dangerous_to_run_now": dangerous_now,
        "one_safest_next_step": next_step,
        "evidence": {
            "model_usage": model_usage,
            "persistent_budget_windows": windows,
            "readiness": readiness,
        },
        "prohibited_actions": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
    }


def _looks_like_operator_programming_task(text: str) -> bool:
    if "safe_system_check" in text:
        return False
    has_programming_word = any(
        term in text
        for term in (
            "patch proposal",
            "patch",
            "diff",
            "routing bug",
            "bug",
            "implementation plan",
            "source review",
            "explicit multi-file review",
            "multi-file review",
            "какие файлы менять",
            "какие тесты добавить",
            "план реализации",
            "предложи исправление",
            "предложение исправ",
            "патч",
            "баг",
            "исправ",
        )
    )
    has_file_mention = bool(AgentLoop._extract_path_mentions(text))
    return has_programming_word and has_file_mention


def _operator_programming_task_payload(
    block: str,
    agent: AgentLoop,
    workspace: Path,
) -> dict:
    del workspace
    text = block.casefold()
    goal = _operator_programming_goal(text)
    mode = (
        "explicit_multi_file_review"
        if AgentLoop._is_explicit_multi_file_mode(block)
        else "source_review"
    )
    constraints = _extract_operator_task_constraints(text)
    for item in (
        "read-only planning digest",
        "no file/code changes",
        "no shell execution",
        "no repair",
        "no allow-effects",
        "no subagents",
        "no automatic persistent memory writes",
    ):
        if item not in constraints:
            constraints.append(item)
    file_evidence = _operator_task_file_evidence(block, agent)
    valid_files = file_evidence["files"]
    return {
        "goal": goal,
        "mode": mode,
        "raw_task": block,
        "files": [item["path"] for item in valid_files],
        "rejected_files": file_evidence["rejected"],
        "constraints": constraints,
        "output_contract": [
            "functions/files to inspect/change",
            "tests to add",
            "minimal diff-plan",
            "risks",
            "actions forbidden without approval",
        ],
        "functions_files_to_inspect_change": _operator_patch_file_plan(valid_files),
        "tests_to_add": _operator_patch_tests_to_add(valid_files, text),
        "minimal_diff_plan": _operator_patch_diff_plan(valid_files, text),
        "risks": [
            "Task-block classifier can over-route broad programming language if tests do not pin negative cases.",
            "Patch proposal is evidence-limited to files that passed workspace preflight.",
            "No code should be changed from this report without a separate approval/apply step.",
        ],
        "actions_forbidden_without_approval": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
        "evidence": {
            "files": valid_files,
            "rejected_files": file_evidence["rejected"],
            "validation": "workspace-relative read-only file preflight",
        },
        "prohibited_actions": [
            "file_write",
            "shell_exec",
            "repair",
            "allow-effects",
            "subagent execution",
            "persistent memory write",
        ],
    }


def _operator_programming_goal(text: str) -> str:
    if any(term in text for term in ("patch", "патч", "diff", "исправ", "bug", "баг")):
        return "patch_proposal"
    if any(term in text for term in ("implementation plan", "план реализации")):
        return "implementation_plan"
    return "source_review"


def _operator_task_file_evidence(block: str, agent: AgentLoop) -> dict:
    root = agent._file_read_workspace_root()
    requested_paths = AgentLoop._extract_path_mentions(block)
    if root is None:
        return {
            "files": [],
            "rejected": [
                {"path": path, "reason": "file_read is not registered"}
                for path in requested_paths
            ],
        }
    files: list[dict] = []
    rejected: list[dict] = []
    seen: set[str] = set()
    for raw_path in requested_paths:
        item = agent._validate_user_file_path(raw_path, workspace=root)
        if item["ok"] is not True:
            rejected.append({"path": raw_path, "reason": str(item["reason"])})
            continue
        target: Path = item["target"]
        rel_path = item["relative_path"].as_posix()
        key = rel_path.casefold()
        if key in seen:
            continue
        seen.add(key)
        try:
            text = target.read_text(encoding="utf-8", errors="strict")
        except UnicodeDecodeError:
            rejected.append({"path": raw_path, "reason": "file is not valid UTF-8"})
            continue
        files.append({
            "source_id": f"file:{rel_path}",
            "path": rel_path,
            "bytes": len(text.encode("utf-8")),
            "lines": len(text.splitlines()),
            "preview": _compact_one_line(text, limit=120),
        })
    return {"files": files, "rejected": rejected}


def _operator_patch_file_plan(files: list[dict]) -> list[str]:
    if not files:
        return ["No valid workspace files passed preflight; inspect files explicitly before proposing a patch."]
    plan: list[str] = []
    for item in files:
        path = item["path"]
        lowered = path.casefold()
        if lowered.endswith("core/operator_intent.py"):
            plan.append(f"{path} - classify operator task/source-review/patch wording before project_health.")
        elif lowered.endswith("main.py"):
            plan.append(f"{path} - dispatch and format programming operator-task reports read-only.")
        elif "test" in lowered:
            plan.append(f"{path} - add routing and operator-task regression tests.")
        else:
            plan.append(f"{path} - inspect as explicit task evidence before any patch.")
    return plan


def _operator_patch_tests_to_add(files: list[dict], text: str) -> list[str]:
    tests = [
        ":operator-task block containing patch proposal + filenames returns goal=patch_proposal.",
        ":operator-task block containing explicit multi-file review validates mentioned workspace files.",
        "safe system check block still returns goal=safe_system_check.",
    ]
    if any("operator_intent.py" in item["path"] for item in files) or "routing" in text:
        tests.append("routing/source-review wording is not captured by project_health.")
    return tests


def _operator_patch_diff_plan(files: list[dict], text: str) -> list[str]:
    del text
    if not files:
        return ["Stop: collect valid file evidence first; do not generate a patch from missing files."]
    return [
        "Add a small task classifier before the generic safe_system_check path.",
        "Extract and validate only user-mentioned file paths inside the workspace.",
        "Return a read-only patch proposal report with files, tests, risks, and forbidden actions.",
        "Keep file_write/shell_exec/repair out of the operator-task path.",
        "Run targeted operator tests, then the full pytest suite.",
    ]


def _extract_operator_task_checks(text: str) -> list[str]:
    mapping = (
        ("architecture", ("architecture", "архитектур")),
        ("runtime", ("runtime", "автоном", "рантайм")),
        ("budget", ("budget", "бюджет", "лимит", "токен", "cost")),
        ("model usage", ("model usage", "модел", "токен")),
        ("approvals", ("approval", "approvals", "одобр", "разреш")),
        ("source registry", ("source registry", "source-registry", "источник")),
        ("scheduler", ("scheduler", "schedule", "распис")),
        ("task queue", ("task queue", "queue", "очеред")),
        ("tests", ("tests", "тест", "pytest")),
    )
    checks = [name for name, terms in mapping if any(term in text for term in terms)]
    return checks or ["architecture", "runtime", "budget", "approvals", "source registry"]


def _extract_operator_task_constraints(text: str) -> list[str]:
    constraints: list[str] = []
    if any(term in text for term in ("ничего не меняй", "no changes", "do not change")):
        constraints.append("no file/code changes")
    if any(term in text for term in ("ничего не запис", "no writes", "do not write")):
        constraints.append("no writes except normal audit logs")
    if "approval" in text or "одобр" in text:
        constraints.append("require approval before any effect")
    if any(term in text for term in ("без repair", "no repair", "не чини")):
        constraints.append("no repair")
    if any(term in text for term in ("без subagent", "no subagent", "не запускай sub")):
        constraints.append("no subagents")
    if any(term in text for term in ("без allow-effects", "no allow-effects", "не включай эффекты")):
        constraints.append("no allow-effects")
    return constraints


def _operator_task_next_step(requires_attention: list[str], dangerous_now: list[str]) -> str:
    for item in requires_attention + dangerous_now:
        lowered = item.casefold()
        if "budget" in lowered or "hour/day" in lowered or "лимит" in lowered:
            return "Configure persistent budget limits before any long unattended operator session."
    if requires_attention:
        return requires_attention[0]
    return "Run the same operator task again after the next code change to confirm the state stayed stable."


def _format_operator_task_report(payload: dict) -> str:
    if payload.get("goal") != "safe_system_check":
        return _format_operator_programming_task_report(payload)
    lines = [
        "=== operator task report ===",
        f"goal: {payload.get('goal')}",
        "constraints:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    lines.append("checks:")
    lines.extend(f"  - {item}" for item in payload.get("checks", []))
    lines.append("1. what works normally:")
    lines.extend(f"  - {item}" for item in payload.get("what_works_normally", []))
    lines.append("2. what requires attention:")
    attention = payload.get("what_requires_attention", [])
    lines.extend(f"  - {item}" for item in (attention or ["nothing urgent"]))
    lines.append("3. what is dangerous to run now:")
    dangerous = payload.get("what_is_dangerous_to_run_now", [])
    lines.extend(f"  - {item}" for item in (dangerous or ["nothing identified"]))
    lines.append("4. one safest next step:")
    lines.append(f"  - {payload.get('one_safest_next_step')}")
    return "\n".join(lines)


def _format_operator_programming_task_report(payload: dict) -> str:
    lines = [
        "=== operator task report ===",
        f"goal: {payload.get('goal')}",
        f"mode: {payload.get('mode')}",
        "constraints:",
    ]
    lines.extend(f"  - {item}" for item in payload.get("constraints", []))
    files = payload.get("evidence", {}).get("files", [])
    lines.append("validated files:")
    if files:
        for item in files:
            lines.append(
                f"  - {item.get('source_id')} "
                f"bytes={item.get('bytes')} lines={item.get('lines')}"
            )
    else:
        lines.append("  - none")
    rejected = payload.get("rejected_files", [])
    if rejected:
        lines.append("rejected files:")
        lines.extend(
            f"  - {item.get('path')}: {item.get('reason')}"
            for item in rejected
        )
    lines.append("1. functions/files to inspect/change:")
    lines.extend(f"  - {item}" for item in payload.get("functions_files_to_inspect_change", []))
    lines.append("2. tests to add:")
    lines.extend(f"  - {item}" for item in payload.get("tests_to_add", []))
    lines.append("3. minimal diff-plan:")
    lines.extend(f"  {idx}. {item}" for idx, item in enumerate(payload.get("minimal_diff_plan", []), start=1))
    lines.append("4. risks:")
    lines.extend(f"  - {item}" for item in payload.get("risks", []))
    lines.append("5. actions forbidden without approval:")
    lines.extend(f"  - {item}" for item in payload.get("actions_forbidden_without_approval", []))
    return "\n".join(lines)
