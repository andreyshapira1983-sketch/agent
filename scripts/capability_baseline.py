#!/usr/bin/env python3
"""Run the capability bench and print where the agent stands today.

Operator instruction 2026-08-05: do not choose a fix direction by opinion —
turn it into a measurable experiment. This is step two, the baseline: how many
of the 40 tasks in `tests/capability_tasks.py` the system gets right
BEFORE anything is changed.

Two axes are scored, and they are not the same question.

**Verdict** — did the system reach the right yes/no? A claim that holds should
end `verified`; one that does not should end anywhere else. For the two
loop-level categories the equivalent is whether the mismatch was detected and
whether the next attempt is constrained.

**Reason** — did the system produce a sentence the agent could REPAIR itself
from? This is the operator's own criterion: if the agent gets the reason for
its error, fixes its reasoning and does better on a similar task, capability
grew; if only the label moves, the instrument got honest and the agent did not.
So the reason column is scored separately and is never inferred from a correct
verdict.

Run:  python scripts/capability_baseline.py [--verbose]

Read-only: no store is written, no model is called, nothing costs money.
"""
from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.evidence import ProvenanceChain, make_evidence  # noqa: E402
from core.reasoning_action_check import check_reasoning_actions  # noqa: E402
from core.replan import ReplanPolicy, ReplanTrigger, format_replan_context  # noqa: E402
from core.verifier_core import verify  # noqa: E402
from tests.capability_tasks import ALL_TASKS, CATEGORIES, Task  # noqa: E402

_CLAIM_CATEGORIES = ("inference", "arithmetic", "contradiction")


def _run_claim(task: Task) -> tuple[bool, bool, str]:
    """(verdict correct?, a repairable reason was produced?, what came back)."""
    chain = ProvenanceChain()
    chain.add(make_evidence(kind="file", source_id="src.txt", obtained_via="tool",
                            claim="contents of src.txt", excerpt=task.excerpt))
    report = verify(answer=f"{task.claim} [file:src.txt]", chain=chain,
                    expects_contract_headers=False)
    if not report.chunks:
        return False, False, "no chunk"
    chunk = report.chunks[0]
    said_holds = chunk.verdict == "verified"

    # `ClaimChunk` is (text, citations, matched_evidence_ids, verdict). There is
    # no field a reason could live in, so this axis cannot score above zero
    # without a change to the data structure — measured, not assumed.
    reason = bool(getattr(chunk, "reason", "") or getattr(chunk, "explanation", ""))
    return said_holds == task.holds, reason, chunk.verdict


def _run_reasoning_action(task: Task) -> tuple[bool, bool, str]:
    report = check_reasoning_actions(task.payload["reasoning"],
                                     task.payload["tools_used"])
    consistent = not report.has_mismatch
    named = bool(report.unjustified_actions or report.mentioned_but_not_planned)
    return consistent == task.holds, named, (
        f"unjustified={list(report.unjustified_actions)} "
        f"promised_not_done={list(report.mentioned_but_not_planned)}"
    )


def _run_error_reuse(task: Task) -> tuple[bool, bool, str]:
    history = [
        ReplanTrigger(code=code, step_id=f"s{i}", tool_name="file_read",
                      arguments={"path": "missing.txt"}, reason=f"{code} happened",
                      attempt=i + 1)
        for i, code in enumerate(task.payload["codes"])
    ]
    decision = ReplanPolicy().decide(history, task.payload["attempts"])
    context = format_replan_context(
        history, task.payload["attempts"] + 1, 5,
        advice=getattr(decision, "advice_for_planner", "") or "",
        forbidden_actions=tuple(getattr(decision, "forbidden_actions", ()) or ()),
    )

    ok = True
    if task.payload.get("must_stop") or task.payload.get("must_abort"):
        ok = ok and decision.action.startswith("abort")
    if task.payload.get("must_forbid_repeat"):
        ok = ok and bool(decision.forbidden_actions)
    # The reason axis: does the block handed to the NEXT attempt carry the
    # failure's stated cause, not merely its code?
    carried = bool(context) and any(h.reason in context for h in history)
    return ok, carried, (
        f"action={decision.action} "
        f"forbidden={len(decision.forbidden_actions)} "
        f"advice={len(decision.advice_for_planner)}ch ctx={len(context)}ch"
    )


_RUNNERS = dict.fromkeys(_CLAIM_CATEGORIES, _run_claim)
_RUNNERS["reasoning_action"] = _run_reasoning_action
_RUNNERS["error_reuse"] = _run_error_reuse


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--verbose", action="store_true", help="print every task")
    args = ap.parse_args(argv)

    verdict_ok: Counter[str] = Counter()
    reason_ok: Counter[str] = Counter()
    total: Counter[str] = Counter()
    misses: list[tuple[Task, str]] = []

    for task in ALL_TASKS:
        ok, reason, detail = _RUNNERS[task.category](task)
        total[task.category] += 1
        verdict_ok[task.category] += ok
        reason_ok[task.category] += reason
        if not ok:
            misses.append((task, detail))
        if args.verbose:
            mark = "ok " if ok else "MISS"
            print(f"  {mark} {task.id:20} {task.claim[:44]:46} {detail}")

    print(f"\n{'category':18} {'verdict':>12}   {'reason given':>14}")
    print("  " + "-" * 46)
    for cat in CATEGORIES:
        n = total[cat]
        print(f"  {cat:18} {verdict_ok[cat]:3d} / {n:<3d}      "
              f"{reason_ok[cat]:3d} / {n:<3d}")
    print("  " + "-" * 46)
    print(f"  {'TOTAL':18} {sum(verdict_ok.values()):3d} / {len(ALL_TASKS):<3d}"
          f"      {sum(reason_ok.values()):3d} / {len(ALL_TASKS):<3d}")

    if misses:
        print(f"\nwrong verdicts ({len(misses)}):")
        for task, detail in misses:
            want = "should hold" if task.holds else "should NOT hold"
            print(f"  {task.id:20} {task.claim[:40]:42} {want:16} -> {detail}")
            print(f"  {'':20} needed: {task.reason_needed}")
    return 0


if __name__ == "__main__":                                   # pragma: no cover
    raise SystemExit(main())
