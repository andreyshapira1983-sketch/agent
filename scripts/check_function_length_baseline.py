"""Function line counts vs a ratchet. Read-only; does not modify the repo.

Why functions and not just files. The file guard
(`scripts/check_ceo_file_baseline.py`) answers "is this module too big", which
is a different question from "can a human hold this in their head". Measured
2026-08-04 across 9 797 functions in the repo: 99.3% are under 100 lines, and
the pain is concentrated in a handful — `core/loop.py:_run_inner` alone is
2 213 lines, 3.4x the next-longest function. A 3 000-line file of 100-line
functions reads fine; a 2 000-line file that is one function does not.

The list is a RATCHET: each ceiling is the measured length plus small slack, so
the guard's one job is "this function may not grow back". When a split lands,
LOWER the ceiling to bank the win — and drop the entry entirely once the
function falls under `REPORT_THRESHOLD`, since anything below that is not worth
watching.

Usage:
    python scripts/check_function_length_baseline.py          # check the ratchet
    python scripts/check_function_length_baseline.py --top 20 # longest functions
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: Below this a function is unremarkable and stays out of the watch list.
REPORT_THRESHOLD = 150

#: Directories that are not ours to police.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "htmlcov", "logs", "data",
})

#: "path:function" -> ceiling. Measured 2026-08-04.
WATCH: dict[str, int] = {
    "core/loop.py:_run_inner": 2223,  # цель — разбор на этапы
    "core/step_sanitizer.py:sanitize_step": 668,
    "core/loop_step_execution.py:_execute_step": 568,
    "agent_tick.py:run_tick": 474,
    "core/loop.py:_synthesize": 430,  # следующий кусок разбора
    "core/self_build_producer.py:produce_self_apply_proposal": 375,
    "core/campaign.py:run_campaign": 350,
    "cli/command_dispatch.py:handle_meta_command": 349,
    "core/evidence.py:evidence_from_tool_result": 321,
    "core/verifier_core.py:verify": 283,
    "app/bootstrap.py:build_agent": 246,
    "core/loop_methods2.py:_record_experience_memory": 246,
    "core/referent_resolver.py:resolve": 240,
    "core/loop.py:__init__": 238,
    "core/self_apply_lane.py:run_self_apply_lane": 235,
    "core/model_router.py:for_task": 227,
    "core/loop_methods2.py:_retrieve_experience_memory": 218,
    "core/work_session.py:run_work_session": 211,
    "core/architecture_audit.py:_build_checks": 197,
    "core/self_task_builder.py:build_coding_task": 195,
    "core/subagent_runner.py:run": 177,
    "core/operator_intent.py:route_operator_intent": 175,
    "core/self_repair.py:run": 173,
    "core/self_build_producer.py:_critic_review": 172,
    "core/completion_obligation.py:evaluate_completion_obligations": 171,
    "core/self_apply_bridge.py:run_approved_self_apply": 170,
    "core/self_repair.py:_execute_tool": 170,
    "core/autonomous_runtime.py:_task_propose": 167,
    "core/memory_policy.py:decide": 167,
    "core/self_task_producer.py:produce_coding_task": 167,
    "cli/repl.py:run_repl": 166,
    "core/role_router.py:route": 163,
    "core/low_evidence_policy.py:evaluate_low_evidence_policy": 161,
    "core/planner.py:plan": 159,
}


def measure(root: Path) -> dict[str, int]:
    """Longest definition per `path:name`, for every Python file in the tree."""
    found: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:                      # pragma: no cover - broken file
            continue
        rel = path.relative_to(root).as_posix()
        for node in ast.walk(tree):
            if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            key = f"{rel}:{node.name}"
            lines = node.end_lineno - node.lineno + 1
            found[key] = max(found.get(key, 0), lines)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=0,
                        help="print the N longest functions instead of checking")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    found = measure(root)

    if args.top:
        for key, n in sorted(found.items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"{n:5d}  {key}")
        return 0

    exit_code = 0
    for key, ceiling in WATCH.items():
        n = found.get(key)
        if n is None:
            print(f"GONE      —   / {ceiling}  {key}  (split or renamed: drop the entry)")
            continue
        flag = "ok"
        if n > ceiling:
            flag, exit_code = "REVIEW", 1
        print(f"{flag:6s}  {n:5d} / {ceiling}  {key}")

    unwatched = sorted(
        ((k, n) for k, n in found.items()
         if n > REPORT_THRESHOLD and k not in WATCH),
        key=lambda kv: -kv[1],
    )
    for key, n in unwatched:
        print(f"NEW     {n:5d} / —      {key}  (over {REPORT_THRESHOLD}: add or split)")
        exit_code = 1

    return exit_code


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
