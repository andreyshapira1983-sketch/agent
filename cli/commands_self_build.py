"""``:self-build-produce`` REPL command (TD-025, grounded default TD-036).

A single narrow operator trigger that runs the subagent-backed self-apply
*producer*: a Manager/Researcher/Builder/Critic/Reporter pipeline that generates
at most one validated low-risk full-content proposal and drops it into the
approval inbox as an ``operation="self_apply_lane.run"`` item.

The Manager picks its target + diagnosis from a *grounded* backlog candidate
(TECH_DEBT.md / knowledge/generated/AGENT_ANATOMY.md) by default — it never invents a diagnosis
via the LLM. When the grounded path yields no publishable target (empty backlog,
or a candidate that is off-allowlist / critical) the command returns ``no_patch``
with the precise grounded reason instead of falling back to the LLM.

It is deliberately narrow:

* it accepts NO arguments and NO free-text patch;
* it ONLY creates one approval inbox item — it never applies the patch, never
  runs the lane, never commits/pushes/merges;
* it is NOT wired into any daemon / scheduler / agent_tick path.

The applied step stays behind the existing human-in-the-loop ``:self-apply-run``
command (TD-024).
"""
from __future__ import annotations

import difflib
import json
import re
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from app.task_scheduler_cli import _scheduler_for, _task_queue_for
from cli.commands_approval import _approval_inbox_for
from cli.commands_budget import _budget_ledger_snapshot
from cli.parsers import _split_meta_args
from cli.self_build_memory import (
    recent_self_build_lessons,
    recently_vetoed_self_build_targets,
    record_self_build_episode,
)
from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS
from core.self_build_producer import produce_self_apply_proposal
from core.self_build_supervisor import evaluate_self_build_supervisor

if TYPE_CHECKING:
    from core.loop import AgentLoop


def _handle_self_build_produce(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    # Narrow trigger: no arguments, no free-text patch. Anything extra is a
    # misuse and is rejected outright.
    if rest.strip():
        print(
            "Usage: :self-build-produce\n"
            "  (no arguments; produces at most one self-apply approval item)",
            file=sys.stderr,
        )
        return True

    snapshot = _budget_ledger_snapshot(agent)
    kill_state = BudgetKillSwitch(path=default_path(workspace)).status(snapshot)
    inbox = _approval_inbox_for(agent, workspace)

    report = produce_self_apply_proposal(
        workspace=workspace,
        inbox=inbox,
        llm=agent.model_router.for_role("synthesizer"),
        vcs=SafeVCS(workspace=workspace),
        budget_snapshot=snapshot,
        kill_switch=kill_state,
        lessons_provider=lambda target: recent_self_build_lessons(agent, target),
        recently_vetoed_targets=recently_vetoed_self_build_targets(agent),
        max_builder_attempts=2,
    )
    result = report.to_dict()

    # Surface the grounded Manager decision (TD-036) so the operator can see
    # whether a verifiable backlog candidate drove the run, and — when it did not
    # produce a patch — the precise grounded reason (empty backlog, off-allowlist
    # target, or critical target) rather than a vague message.
    manager = next(
        (r for r in (result.get("roles") or []) if r.get("role") == "manager"),
        None,
    )
    manager_data = (manager or {}).get("data", {})
    grounded = bool(manager_data.get("grounded"))
    evidence_ref = manager_data.get("evidence_ref") or ""
    manager_detail = (manager or {}).get("detail") or ""
    # True when the grounded path ran but yielded no publishable target. This is
    # NOT necessarily an empty backlog — a candidate may have been found and then
    # rejected as off-allowlist/critical; manager_detail carries the exact reason.
    no_grounded_target = (
        result.get("status") in {"no_patch", "no_grounded_target"}
        and manager is not None
        and manager.get("decision") == "no_target"
        and not grounded
    )

    # Secret-free structured log (never dumps generated file content).
    agent.log.log(
        "self_build_produce",
        {
            "status": result.get("status"),
            "target_path": result.get("target_path"),
            "approval_id": result.get("approval_id"),
            "checked_gates": result.get("checked_gates"),
            "veto_reasons": result.get("veto_reasons"),
            "grounded": grounded,
            "evidence_ref": evidence_ref,
            "no_grounded_target": no_grounded_target,
            "attempts": result.get("attempts"),
        },
    )

    # Journal the attempt (and WHY it did/didn't produce a patch) into episodic
    # memory so the agent accumulates its own lessons instead of relying on a
    # human to re-investigate every outcome.
    record_self_build_episode(agent, kind="self-build-produce", result=result)

    lines = [
        "=== self-build produce ===",
        f"status: {result.get('status')}",
        f"reason: {result.get('reason')}",
    ]
    if grounded:
        lines.append("manager: grounded backlog candidate")
        if evidence_ref:
            lines.append(f"evidence_ref: {evidence_ref}")
    elif no_grounded_target:
        detail = manager_detail or "no verifiable grounded candidate"
        lines.append(f"manager: no grounded target ({detail})")
    if result.get("target_path"):
        lines.append(f"target: {result.get('target_path')}")
    if result.get("approval_id"):
        lines.append(f"approval_id: {result.get('approval_id')}")
    if result.get("veto_reasons"):
        lines.append(f"veto_reasons: {result.get('veto_reasons')}")
    roles = result.get("roles") or []
    if roles:
        lines.append(
            "roles: "
            + ", ".join(f"{r['role']}:{r['decision']}" for r in roles)
        )
    lines.append(f"next: {result.get('next_human_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True


# ── :self-build-supervisor ───────────────────────────────────────────────────
# Read-only supervisor cycle, extracted verbatim from main.py. It decides
# whether to wait, stop, or propose one evidence-backed candidate; the helpers
# below are its read-only evidence collectors.


def _tech_debt_summary(workspace: Path) -> dict:
    """Best-effort, read-only TECH_DEBT.md digest: counts of TD entries by status."""
    path = workspace / "TECH_DEBT.md"
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return {"present": False}
    td_re = re.compile(r"^\s*TD-\d+\b")
    status_re = re.compile(r"^\s*Статус\s*:\s*(.+?)\s*$", re.IGNORECASE)
    total = open_count = done_count = 0
    awaiting_status = False
    for line in text.splitlines():
        if td_re.match(line):
            total += 1
            awaiting_status = True
            continue
        if awaiting_status:
            match = status_re.match(line)
            if match:
                if match.group(1).strip().lower().startswith("done"):
                    done_count += 1
                else:
                    open_count += 1
                awaiting_status = False
    return {
        "present": True,
        "total": total,
        "done": done_count,
        "open": open_count,
    }


def _recent_error_lines(workspace: Path, *, max_errors: int = 5) -> list[str]:
    """Best-effort scan of the newest trace log for recent error events.

    Read-only and defensive: any failure returns an empty list. Only compact,
    log-safe identifiers (event name + optional error type) are surfaced.
    """
    try:
        log_dir = workspace / "logs"
        candidates = sorted(
            (p for p in log_dir.glob("*.jsonl") if p.is_file()),
            key=lambda p: p.stat().st_mtime,
            reverse=True,
        )
    except OSError:
        return []
    if not candidates:
        return []
    errors: list[str] = []
    try:
        lines = candidates[0].read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except (ValueError, TypeError):
            continue
        name = str(event.get("event") or event.get("type") or "")
        if not name:
            continue
        lowered = name.lower()
        if "error" in lowered or "fail" in lowered or "blocked" in lowered:
            errors.append(name)
            if len(errors) >= max_errors:
                break
    return errors


def _handle_self_build_supervisor(rest: str, agent: AgentLoop, workspace: Path) -> bool:
    """Read-only supervisor cycle: decide whether to wait, stop, or propose one
    evidence-backed self-build candidate. Never applies changes / runs tests /
    writes files / calls shell_exec / refreshes models."""
    as_json = "--json" in _split_meta_args(rest)
    budget_windows = _budget_ledger_snapshot(agent)
    approvals_pending = int(
        _approval_inbox_for(agent, workspace).snapshot().get("pending", 0) or 0
    )
    task_queue = _task_queue_for(agent, workspace).summary()
    scheduler = _scheduler_for(agent, workspace).summary()
    recent_errors = _recent_error_lines(workspace)
    tech_debt = _tech_debt_summary(workspace)

    report = evaluate_self_build_supervisor(
        budget_windows=budget_windows,
        approvals_pending=approvals_pending,
        task_queue=task_queue,
        scheduler=scheduler,
        recent_errors=recent_errors,
        tech_debt=tech_debt,
        candidate_provider=lambda: _self_build_propose_payload(workspace),
    )

    # Log a compact, secret-free copy: never dump a full candidate diff.
    candidate = report.get("candidate")
    if isinstance(candidate, dict):
        candidate_log = {
            "file": candidate.get("file"),
            "has_diff": candidate.get("diff") not in (None, "NO_PATCH"),
        }
    else:
        candidate_log = candidate
    agent.log.log(
        "self_build_supervisor",
        {
            "status": report.get("status"),
            "reason": report.get("reason"),
            "checked_sections": report.get("checked_sections"),
            "candidate": candidate_log,
            "recommended_next_action": report.get("recommended_next_action"),
        },
    )

    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2), file=sys.stderr)
        return True

    lines = [
        "=== self-build supervisor ===",
        f"status: {report.get('status')}",
        f"reason: {report.get('reason')}",
        f"checked: {', '.join(report.get('checked_sections') or [])}",
    ]
    if isinstance(candidate, dict):
        lines.append(f"candidate file: {candidate.get('file')}")
        lines.append(f"candidate diagnosis: {candidate.get('diagnosis')}")
    else:
        lines.append(f"candidate: {candidate}")
    lines.append(f"next: {report.get('recommended_next_action')}")
    print("\n".join(lines), file=sys.stderr)
    return True

# ---------------------------------------------------------------------------
# Large-file scan + `:self-build-propose`, moved here from
# cli/commands_ingest.py on 2026-08-20. It had always belonged to this module:
# cli/commands_self_build.py already imported `_self_build_propose_payload`
# back out of the ingest file.
# ---------------------------------------------------------------------------
LARGE_FILE_LINE_THRESHOLD = 3000
SAFE_PYTHON_SCAN_ROOTS = (
    "api",
    "bug_lab",
    "cli",
    "core",
    "scripts",
    "tests",
    "tools",
)
UNSAFE_SCAN_DIRS = {
    ".agents",
    ".codex",
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".ruff_cache",
    ".venv",
    "__pycache__",
    "config",
    "data",
    "logs",
}


def _handle_self_build_propose(
    rest: str, agent: AgentLoop | None, workspace: Path
) -> bool:
    """Propose a self-build target. Reachable by TWO paths, hence the `| None`.

    `cli/command_dispatch.py` calls it like every other handler, with a live
    agent. `cli/app.py` calls it as a fast path BEFORE an agent exists, so that
    `:self-build-propose` costs no `.env` load and no agent build — and passed
    `None` behind a `# type: ignore[arg-type]`, which silenced the checker
    instead of admitting the second caller. The annotation says it now.

    The parameter stays in the signature: 61 dispatcher call sites share the
    `(rest, agent, workspace)` shape, and breaking that for the one handler
    that ignores its agent would cost more than it buys.
    """
    del agent
    tokens = _split_meta_args(rest)
    if "--large-files" in tokens:
        print(_format_large_file_report(_large_file_report(workspace)), file=sys.stderr)
        return True
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


def _large_file_report(
    workspace: Path,
    *,
    threshold: int = LARGE_FILE_LINE_THRESHOLD,
) -> list[dict[str, int | str]]:
    items: list[dict[str, int | str]] = []
    for path in _iter_safe_python_files(workspace):
        line_count = _count_lines(path)
        if line_count > threshold:
            items.append(
                {
                    "path": path.relative_to(workspace).as_posix(),
                    "lines": line_count,
                }
            )
    return sorted(items, key=lambda item: (-int(item["lines"]), str(item["path"])))


def _iter_safe_python_files(workspace: Path):
    for path in sorted(workspace.glob("*.py")):
        if _safe_python_file(path):
            yield path
    for name in SAFE_PYTHON_SCAN_ROOTS:
        root = workspace / name
        if not _safe_scan_dir(root):
            continue
        yield from _iter_python_files_under(root)


def _iter_python_files_under(root: Path):
    stack = [root]
    while stack:
        current = stack.pop()
        if not _safe_scan_dir(current):
            continue
        dirs = []
        files = []
        for child in current.iterdir():
            if child.is_dir():
                if _safe_scan_dir(child):
                    dirs.append(child)
                continue
            if child.suffix == ".py" and _safe_python_file(child):
                files.append(child)
        for path in sorted(files):
            yield path
        stack.extend(sorted(dirs, reverse=True))


def _safe_scan_dir(path: Path) -> bool:
    return (
        path.exists()
        and path.is_dir()
        and not path.is_symlink()
        and path.name not in UNSAFE_SCAN_DIRS
        and not path.name.startswith(".")
    )


def _safe_python_file(path: Path) -> bool:
    if path.suffix != ".py" or path.is_symlink() or not path.is_file():
        return False
    parts = set(path.parts)
    return not any(part in UNSAFE_SCAN_DIRS or part.startswith(".") for part in parts)


def _count_lines(path: Path) -> int:
    with path.open("rb") as fh:
        return sum(1 for _ in fh)


def _format_large_file_report(items: list[dict[str, int | str]]) -> str:
    if not items:
        return "NO_LARGE_FILES"
    lines = [
        "LARGE_FILE_REPORT",
        f"threshold_lines={LARGE_FILE_LINE_THRESHOLD}",
    ]
    lines.extend(f"{item['path']} lines={item['lines']}" for item in items)
    return "\n".join(lines)


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
