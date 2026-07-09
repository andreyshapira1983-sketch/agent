"""Stage B of the coding-skill ladder (roadmap Ступень 1): write code to make a
HUMAN-APPROVED, FROZEN acceptance test pass.

Stage A (:mod:`core.self_task_producer`) turned a real ``# TODO`` into a coding
task plus a failing acceptance test and dropped ONE inert
``self_build_task.approve`` inbox item. A human read and approved that test
BEFORE any implementation existed — that is the anti-cheating guarantee.

Stage B consumes exactly one such *approved* item and:

* asks the Builder to produce the COMPLETE new content of the target file so the
  frozen test passes (existing behaviour and public API preserved),
* critic-validates that implementation cheaply (parses, low-risk, confident),
* publishes ONE ``self_apply_lane.run`` proposal carrying BOTH the new
  implementation and the frozen test, targeted at the new test file.

The existing self-apply lane then applies the change on a temp branch, runs the
targeted test plus the full suite, and auto-rolls-back on any failure. Stage B
therefore never applies anything itself and never mutates the frozen test — it
only proposes an implementation for the already-blessed yardstick.

Design constraints mirror Stage A and the self-build producer:

* consumes only an APPROVED ``self_build_task.approve`` item (no free-text);
* the implementation target is fixed to the file the task named — no LLM target;
* the frozen test is read via :func:`core.self_task_producer.decode_frozen_test`
  so a redaction-mangled preview is never fed to the builder or the lane;
* deterministic gates first (kill-switch, budget, dirty tree);
* every failure is a hard veto that creates NO self-apply item.
"""
from __future__ import annotations

import ast
import hashlib
from pathlib import Path
from typing import Any, Callable

from core.injection_guard import prepare_untrusted_text_for_llm
from core.self_apply_bridge import SELF_APPLY_OPERATION, build_self_apply_payload
from core.self_build_producer import (
    ProducerReport,
    RoleOutput,
    _BUILDER_MAX_TOKENS,
    _DEFAULT_CONFIDENCE_THRESHOLD,
    _MAX_CONTENT_BYTES,
    _default_file_reader,
    _is_self_build_target_allowed,
    _llm_json,
    _looks_like_diff,
)
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)
from core.self_task_producer import SELF_TASK_OPERATION, decode_frozen_test

# Origin stamped on every Stage-B self-apply proposal so provenance is auditable
# and distinct from the split-oriented self-build producer.
TASK_BUILD_ORIGIN = "subagent_self_task_builder"


# ── the builder ──────────────────────────────────────────────────────────────


def _impl_builder_generate(
    llm: Any,
    *,
    impl_path: str,
    frozen_test: str,
    current_content: str,
    task_summary: str,
) -> RoleOutput:
    """Generate the FULL new content of ``impl_path`` so ``frozen_test`` passes.

    The frozen test is authoritative and MUST NOT be modified. Both the current
    file and the test are routed through the injection guard because they are
    untrusted text from the model/disk.
    """
    safe_current, inj = prepare_untrusted_text_for_llm(
        current_content or "",
        source_label=f"self_task_impl:{impl_path}",
    )
    if safe_current is None:
        return RoleOutput(
            "builder",
            "failed",
            "target content blocked by injection guard",
            {"injection": inj.to_log_payload(), "target": impl_path},
        )
    safe_test, inj_test = prepare_untrusted_text_for_llm(
        frozen_test or "",
        source_label=f"self_task_test:{impl_path}",
    )
    if safe_test is None:
        return RoleOutput(
            "builder",
            "failed",
            "frozen test blocked by injection guard",
            {"injection": inj_test.to_log_payload(), "target": impl_path},
        )
    system = (
        "You are the Builder implementing a small coding task. You are given the "
        "CURRENT content of a target Python file and a FROZEN acceptance test "
        "that a human already approved. The test is authoritative: you MUST NOT "
        "change it, and you may not weaken it. Produce the COMPLETE new content "
        "of the target file so that the acceptance test passes, while preserving "
        "ALL existing behaviour, imports and public API of that file. Add only "
        "what the test requires. Return the entire file, never a diff or patch. "
        "Reply with strict JSON only: "
        '{"content": "<full file content>", "reason": "<one sentence>", '
        '"confidence": <0..1>}.'
    )
    user = (
        f"Target file: {impl_path}\n"
        f"Task: {task_summary or '(none)'}\n\n"
        f"Frozen acceptance test (DO NOT MODIFY):\n{safe_test}\n\n"
        f"Current content of {impl_path}:\n{safe_current or '(empty / new file)'}"
    )
    parsed = _llm_json(llm, system=system, user=user, max_tokens=_BUILDER_MAX_TOKENS)
    if not parsed:
        return RoleOutput("builder", "failed", "builder returned no parseable JSON")
    content = parsed.get("content")
    if not isinstance(content, str) or not content.strip():
        return RoleOutput("builder", "failed", "builder returned empty content")
    reason = str(parsed.get("reason") or task_summary or "").strip()
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return RoleOutput(
        "builder",
        "built",
        f"generated {len(content)} chars (confidence {confidence:.2f})",
        {"content": content, "reason": reason, "confidence": confidence},
    )


# ── the critic ───────────────────────────────────────────────────────────────


def _impl_critic_review(
    build: dict[str, Any],
    *,
    impl_path: str,
    current_content: str,
    confidence_threshold: float,
) -> RoleOutput:
    """Cheap pre-filter before a human sees the proposal.

    The real gate is the self-apply lane actually running the frozen test plus
    the full suite; this only rejects obvious garbage so we never publish it.
    """
    veto: list[str] = []
    content = build.get("content") or ""
    confidence = float(build.get("confidence") or 0.0)

    if not _is_self_build_target_allowed(impl_path):
        veto.append(f"impl_path {impl_path!r} is not a low-risk editable file")
    if not isinstance(content, str) or not content.strip():
        veto.append("empty implementation content")
    else:
        if _looks_like_diff(content):
            veto.append("implementation looks like a diff, not a full file")
        if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            veto.append("implementation content is too large")
        try:
            ast.parse(content)
        except SyntaxError as exc:
            veto.append(f"implementation does not parse: {exc.msg}")
        if current_content.strip() and content.strip() == current_content.strip():
            veto.append("implementation is identical to the current file (no-op)")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    decision = "veto" if veto else "pass"
    return RoleOutput(
        "critic",
        decision,
        "; ".join(veto) if veto else "all impl-critic checks passed",
        {"veto_reasons": veto, "confidence": confidence},
    )


# ── the reporter ─────────────────────────────────────────────────────────────


def _impl_reporter_publish(
    inbox: Any,
    *,
    impl_path: str,
    test_path: str,
    frozen_test: str,
    build: dict[str, Any],
    evidence: list[str],
) -> RoleOutput:
    """Publish exactly one ``self_apply_lane.run`` proposal (impl + frozen test)."""
    content = build["content"]
    payload = build_self_apply_payload(
        files=[
            {"path": impl_path, "content": content},
            {"path": test_path, "content": frozen_test},
        ],
        reason=build.get("reason") or "",
        evidence=evidence,
        test_paths=[test_path],
        test_pattern=None,
        origin=TASK_BUILD_ORIGIN,
    )
    digest = hashlib.sha1(
        (impl_path + "\n" + content + "\n" + frozen_test).encode("utf-8")
    ).hexdigest()[:12]
    dedup_key = f"self_task_build:{impl_path}:{digest}"
    summary = f"coding-task implementation for {impl_path} (+ frozen test {test_path})"
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=summary,
        risk="reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=dedup_key,
    )
    return RoleOutput(
        "reporter",
        "published",
        f"created self-apply item {item.id}",
        {"approval_id": item.id, "dedup_key": dedup_key},
    )


# ── orchestration ────────────────────────────────────────────────────────────


def build_coding_task(
    *,
    workspace: str | Path,
    inbox: Any,
    approval_id: str,
    llm: Any,
    vcs: Any = None,
    budget_snapshot: Any = None,
    kill_switch: Any = None,
    file_reader: Callable[[str], str | None] | None = None,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
) -> ProducerReport:
    """Stage B: implement one approved coding task and propose it to the lane.

    Never writes code to disk, never applies a patch, creates at most one
    ``self_apply_lane.run`` inbox item. ``status`` is one of:
    ``budget_kill_switch`` / ``budget_wait`` / ``dirty_tree_wait`` /
    ``not_found`` / ``wrong_operation`` / ``not_approved`` / ``invalid_task`` /
    ``build_veto`` / ``proposed``.
    """
    gates: list[str] = []

    # ── gate 1: budget kill-switch ──────────────────────────────────────────
    if kill_switch is not None and getattr(kill_switch, "active", False):
        return ProducerReport(
            status="budget_kill_switch",
            reason=str(getattr(kill_switch, "reason", "") or "kill-switch active"),
            checked_gates=["kill_switch"],
            next_human_action="Resolve budget kill-switch before building a task.",
        )
    gates.append("kill_switch")

    # ── gate 2: hour budget near-exhaustion ─────────────────────────────────
    if budget_snapshot is not None:
        headroom = hour_budget_headroom(budget_snapshot)
        near, reasons = is_budget_near_exhaustion(headroom)
        if near:
            return ProducerReport(
                status="budget_wait",
                reason="; ".join(reasons) or "budget near exhaustion",
                checked_gates=gates + ["budget"],
                next_human_action="Wait for the budget window to refill.",
            )
    gates.append("budget")

    # ── gate 3: dirty working tree ──────────────────────────────────────────
    if vcs is not None and not vcs.is_clean():
        return ProducerReport(
            status="dirty_tree_wait",
            reason="git working tree is not clean",
            checked_gates=gates + ["dirty_tree"],
            next_human_action="Commit or stash local changes, then retry.",
        )
    gates.append("dirty_tree")

    # ── gate 4: an APPROVED Stage-A task item must exist ─────────────────────
    item = inbox.get(approval_id) if hasattr(inbox, "get") else None
    if item is None:
        return ProducerReport(
            status="not_found",
            reason=f"no approval item with id {approval_id!r}",
            checked_gates=gates,
            next_human_action="Check the id from :self-task-propose / :approval-list.",
        )
    if getattr(item, "operation", "") != SELF_TASK_OPERATION:
        return ProducerReport(
            status="wrong_operation",
            reason=(
                f"item {approval_id!r} is {getattr(item, 'operation', '')!r}, "
                f"not a {SELF_TASK_OPERATION!r} coding task"
            ),
            checked_gates=gates,
            next_human_action="Pass the id of a coding-task item from :self-task-propose.",
        )
    if getattr(item, "status", "") != "approved":
        return ProducerReport(
            status="not_approved",
            reason=f"item status={getattr(item, 'status', '')}; approve it first",
            target_path=str((item.payload or {}).get("impl_path") or "") or None,
            checked_gates=gates,
            next_human_action=f"Approve it first: :approval-approve {approval_id}",
        )
    gates.append("task")

    payload = item.payload or {}
    impl_path = str(payload.get("impl_path") or "").replace("\\", "/").strip()
    test_path = str(payload.get("test_path") or "").replace("\\", "/").strip()
    frozen_test = decode_frozen_test(payload)
    task_summary = str(payload.get("task_summary") or payload.get("task_title") or "")
    evidence_ref = str(payload.get("evidence_ref") or "")

    roles: list[RoleOutput] = []

    # ── validate the frozen spec before spending an LLM call ────────────────
    problems: list[str] = []
    if not impl_path:
        problems.append("task has no impl_path")
    elif not _is_self_build_target_allowed(impl_path):
        problems.append(f"impl_path {impl_path!r} is not a low-risk editable file")
    if not test_path or not test_path.startswith("tests/"):
        problems.append("task has no valid test_path under tests/")
    if not frozen_test.strip():
        problems.append("task carries no frozen test content")
    elif "[REDACTED:" in frozen_test:
        problems.append("frozen test is redaction-corrupted; re-run :self-task-propose")
    if problems:
        return ProducerReport(
            status="invalid_task",
            reason="; ".join(problems),
            target_path=impl_path or None,
            checked_gates=gates,
            veto_reasons=problems,
            next_human_action="This task item is unusable; propose a fresh one.",
        )

    reader = file_reader or _default_file_reader(workspace)
    current_content = reader(impl_path) or ""
    evidence = [
        f"coding_task: {approval_id}",
        f"impl: {impl_path}",
        f"test: {test_path}",
    ]
    if evidence_ref:
        evidence.append(f"grounded: {evidence_ref}")

    # ── builder ──────────────────────────────────────────────────────────────
    builder = _impl_builder_generate(
        llm,
        impl_path=impl_path,
        frozen_test=frozen_test,
        current_content=current_content,
        task_summary=task_summary,
    )
    roles.append(builder)
    if builder.decision != "built":
        return ProducerReport(
            status="build_veto",
            reason=builder.detail or "builder failed",
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=[builder.detail or "builder failed"],
            next_human_action="Builder failed; no self-apply item created.",
        )

    # ── critic ───────────────────────────────────────────────────────────────
    critic = _impl_critic_review(
        builder.data,
        impl_path=impl_path,
        current_content=current_content,
        confidence_threshold=confidence_threshold,
    )
    roles.append(critic)
    if critic.decision == "veto":
        return ProducerReport(
            status="build_veto",
            reason=critic.detail,
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(critic.data.get("veto_reasons", [])),
            next_human_action="Critic vetoed the implementation; no self-apply item created.",
        )

    # ── reporter ─────────────────────────────────────────────────────────────
    reporter = _impl_reporter_publish(
        inbox,
        impl_path=impl_path,
        test_path=test_path,
        frozen_test=frozen_test,
        build=builder.data,
        evidence=evidence,
    )
    roles.append(reporter)
    new_id = reporter.data["approval_id"]
    return ProducerReport(
        status="proposed",
        reason=builder.data.get("reason") or task_summary or "",
        target_path=impl_path,
        approval_id=new_id,
        checked_gates=gates,
        role_outputs=roles,
        next_human_action=(
            f"Review self-apply item {new_id}, approve with "
            f":approval-approve {new_id}, then apply+test with "
            f":self-apply-run {new_id}."
        ),
    )
