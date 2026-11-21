"""Check function line counts against their ratchet ceilings (read-only).

Each ceiling is the measured length plus small slack: a watched function may not
grow back. When a function shrinks, lower its ceiling, and drop the entry once it
is under REPORT_THRESHOLD. Run: python scripts/check_function_length_baseline.py
(exit code 1 on a breach); --top N lists the N longest functions instead.
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: Below this a function is unremarkable and stays out of the watch list.
REPORT_THRESHOLD = 150

#: Not ours to police; `.claude/worktrees/` holds full checkouts that would count twice.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "htmlcov", "logs", "data", ".claude",
    ".autonomous-repair",
})

#: "path:function" -> ceiling. Raise a ceiling only with a one-line reason beside it.
WATCH: dict[str, int] = {
    # Growth: next charter goal chosen inside a run, the goal's success check, sink wiring.
    "agent_tick.py:run_paced_campaign": 207,
    # Growth: own-cache evidence mark, per-run self-defects reset, sensor and spend blocks.
    "core/loop.py:AgentLoop._run_inner": 320,
    # One branch per tool; the named remedy is dict dispatch, not a higher ceiling.
    "core/step_sanitizer.py:sanitize_step": 705,
    # Growth: pending-proposal files passed to the gate, for a proportionate refusal.
    "core/loop_step_execution.py:AgentLoopStepExecution._execute_step": 517,
    # Growth: dry run carried into dedup and stuck-row repair, sink wiring, memory profile.
    "agent_tick.py:run_tick": 424,
    # Moved whole from core/loop.py; growth: sensor citations offered on a no-tool turn.
    "core/loop_synthesis.py:AgentLoopSynthesis._synthesize": 310,
    # Per-file gate 3 stays inline: extracting it would part the gate from its reason text.
    "core/self_build_producer.py:produce_self_apply_proposal": 247,
    # Growth: goal judged and switched inside the run, stall waits, cost caps; extraction
    # would need the loop state as an object.
    "core/campaign.py:run_campaign": 595,
    # A flat command table, two or three lines per command; the remedy is dict dispatch.
    "cli/command_dispatch.py:handle_meta_command": 366,
    # Growth: exhausted actions filtered in admit, goal-named generator, goal origin label.
    "core/best_next_action.py:select_best_next_action": 177,
    # Growth: per-tool evidence branches (convert_file, raw find_in_files output).
    "core/evidence.py:evidence_from_tool_result": 284,
    # Growth: one registration per tool, egress ledger, routing policy, replan/observe flags.
    "app/bootstrap.py:build_agent": 236,
    "core/loop_memory_write.py:AgentLoopMemoryWrite._record_experience_memory": 172,
    "core/referent_resolver.py:ReferentResolver.resolve": 240,
    # Moved whole from core/loop.py; growth: one parameter or field per wired store or flag.
    "core/loop_init.py:AgentLoopInit.__init__": 182,
    # Former run_self_apply_lane body under the lane lock; growth: base-version gate and a
    # rollback that spares others' edits.
    "core/self_apply_lane.py:_run_self_apply_lane_locked": 248,
    # Growth: escaped replay hint, fitness filter inside the search, family warnings.
    "core/loop_memory_read.py:AgentLoopMemoryRead._retrieve_experience_memory": 165,
    # Growth: investigation switch (half-open probe); extracting it would touch six locals.
    "core/work_session.py:run_work_session": 220,
    "core/architecture_audit.py:_build_checks": 197,
    "core/self_task_builder.py:build_coding_task": 195,
    # Growth: an unreadable child evidence chain is journaled, not scored as no citations.
    "core/subagent_runner.py:SubAgentRunner.run": 180,
    "core/operator_intent.py:route_operator_intent": 175,
    "core/self_repair.py:SelfRepairController.run": 173,
    "core/self_build_producer.py:_critic_review": 172,
    # Growth: uncovered task units named, intent read from the demanding part, contract coverage.
    "core/completion_obligation.py:evaluate_completion_obligations": 189,
    "core/self_apply_bridge.py:run_approved_self_apply": 170,
    "core/self_repair.py:SelfRepairController._execute_tool": 170,
    "core/autonomous_runtime_proposals.py:AutonomousRuntimeProposals._task_propose": 160,
    # Growth: verified_diagnosis source, lesson-injection receipts, critic measurement.
    "core/self_task_producer.py:produce_coding_task": 180,
    # The fabricated-citation branch belongs at the answer-acceptance boundary (operator ruling).
    "core/unsupported_claims.py:_enforce_without_contradictions": 170,
    # Crossed 150 when its broad handlers began journaling; splitting the phases is separate work.
    "core/ingestion.py:ingest_web_topic": 155,
    "core/loop_run_tail.py:AgentLoopRunTail._finalize_run_tail": 203,
    # Growth: small-talk classifier on the short path, coherence judged by the step rationale.
    "core/loop_attempt.py:AgentLoopAttempt._run_attempt_loop": 370,
    # Growth: persistence hook (step helper, one fallback of another form, settled exit).
    "core/loop_verify_replan.py:AgentLoopVerifyReplan._verify_and_settle_answer": 385,
}


def _walk(body: list[ast.stmt], prefix: str, rel: str, out: dict[str, int]) -> None:
    """Collect `path:Class.method` lengths; the scope prefix keeps same-named definitions apart."""
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            out[f"{rel}:{name}"] = node.end_lineno - node.lineno + 1
            _walk(node.body, f"{name}.", rel, out)
        elif isinstance(node, ast.ClassDef):
            _walk(node.body, f"{prefix}{node.name}.", rel, out)


def measure(root: Path) -> dict[str, int]:
    """Definition lengths per `path:qualified_name`, for the whole tree."""
    found: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:                      # pragma: no cover - broken file
            continue
        _walk(tree.body, "", path.relative_to(root).as_posix(), found)
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
            # A stale entry watches nothing; fail so it gets removed.
            print(f"GONE      —   / {ceiling}  {key}  (split or renamed: drop the entry)")
            exit_code = 1
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
