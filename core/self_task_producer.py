"""Stage A of the coding-skill ladder (roadmap Ступень 1): propose a grounded
coding TASK plus its acceptance test for HUMAN approval.

This module NEVER writes implementation code and NEVER applies anything. It turns
a real ``# TODO``/``# FIXME`` comment already in the codebase into:

* a plain-language task (title + summary), and
* a *failing* acceptance test that pins down what "done" means,

then drops exactly ONE approval item (``operation="self_build_task.approve"``)
into the approval inbox. A human reviews the test BEFORE any implementation
exists — this is the anti-cheating guarantee: the agent cannot grade its own
homework, because the yardstick (the test) is blessed by a human first.

Stage B (``core/self_task_builder.py``) only runs after the human approves this
item; it writes code to make the FROZEN test pass via the existing self-apply
lane (approval + targeted tests + auto-rollback).

Design constraints mirrored from the self-build producer:

* grounded only — no task without a real code TODO/FIXME evidence anchor;
* no LLM-invented targets — the implementation file is the file the TODO lives in;
* deterministic gates first (kill-switch, budget, dirty tree, one-in-flight);
* every failure is a hard veto that creates NO inbox item.
"""
from __future__ import annotations

import ast
import base64
import hashlib
import re
from pathlib import Path
from typing import Any, Callable

from core.self_apply_bridge import build_self_apply_payload  # noqa: F401  (kept for parity/tests)
from core.self_build_producer import (
    ProducerReport,
    RoleOutput,
    _default_file_reader,
    _is_self_build_target_allowed,
    _llm_json,
    _looks_like_diff,
    _MAX_CONTENT_BYTES,
    _DEFAULT_CONFIDENCE_THRESHOLD,
)
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# The approval operation for a Stage-A coding-task blessing. It is deliberately
# DISTINCT from ``self_apply_lane.run`` so approving it never triggers the lane:
# the item is inert (a blessed spec) until Stage B consumes it.
SELF_TASK_OPERATION = "self_build_task.approve"

TASK_PRODUCER_ORIGIN = "subagent_self_task_producer"

# Only the cleanest evidence source feeds Stage 1 at first: concrete, local
# ``# TODO``/``# FIXME``/``# XXX`` comments. Broader sources (TECH_DEBT.md,
# architecture audit) can be added later once the loop is proven.
_CODE_TODO_SOURCE = "code_todo"


def decode_frozen_test(payload: dict[str, Any]) -> str:
    """Return the exact frozen acceptance test from a Stage-A approval payload.

    Prefers the redaction-inert ``test_content_b64`` blob (the byte-for-byte
    copy) and falls back to the human-readable ``test_content`` field for
    legacy items created before base64 preservation existed. Stage B MUST use
    this so it never feeds a redaction-mangled test to the builder/lane.
    """
    b64 = payload.get("test_content_b64")
    if isinstance(b64, str) and b64.strip():
        try:
            return base64.b64decode(b64.encode("ascii")).decode("utf-8")
        except Exception:  # noqa: BLE001 — fall back to the plaintext preview
            pass
    raw = payload.get("test_content")
    return raw if isinstance(raw, str) else ""


def _default_task_selector(workspace: str | Path) -> Callable[[], Any]:
    """Zero-arg selector yielding the top-ranked ``code_todo`` backlog candidate.

    Read-only, best-effort: any load failure yields ``None`` so the producer
    refuses (``no_task``) rather than inventing a task.
    """

    def _select() -> Any:
        try:
            from core.backlog_selector import load_backlog

            for candidate in load_backlog(workspace):
                if str(getattr(candidate, "signal_source", "")) == _CODE_TODO_SOURCE:
                    return candidate
        except Exception:  # noqa: BLE001 — a broken selector must never break producer
            return None
        return None

    return _select


def _field(obj: Any, name: str) -> str:
    return str(getattr(obj, name, "") or "").strip()


def _has_pending_task(inbox: Any) -> bool:
    """True when an unresolved Stage-A task item already waits for a human."""
    try:
        items = inbox.list()
    except Exception:  # noqa: BLE001
        return False
    for item in items:
        if getattr(item, "operation", "") != SELF_TASK_OPERATION:
            continue
        if getattr(item, "status", "") in ("pending", "approved"):
            return True
    return False


# ── the task builder (LLM) ──────────────────────────────────────────────────


def _task_builder_generate(
    llm: Any, *, impl_path: str, quote: str, evidence_ref: str, current_content: str
) -> RoleOutput:
    """Ask the model for a task spec + a failing acceptance test (never code)."""
    system = (
        "You are the Task Author on a self-build team. You are given a real "
        "TODO/FIXME comment from a Python file and the file's current content. "
        "Propose ONE small, concrete coding task that resolves that comment, and "
        "write a NEW pytest acceptance test that FAILS today and will PASS once "
        "the task is implemented. Do NOT write the implementation itself. The "
        "test must import from the given implementation module and assert real, "
        "specific behaviour (never a trivial `assert True`). Reply with strict "
        "JSON only: {"
        '"task_title": "<short title>", '
        '"task_summary": "<what to implement, 1-3 sentences>", '
        '"impl_path": "<repo-relative .py file to change>", '
        '"test_path": "tests/test_<name>.py", '
        '"test_content": "<full pytest file content>", '
        '"confidence": <0..1>}.'
    )
    user = (
        f"Implementation file: {impl_path}\n"
        f"Evidence (file:line): {evidence_ref}\n"
        f"TODO/FIXME comment: {quote}\n\n"
        f"Current content of {impl_path}:\n{current_content or '(empty)'}"
    )
    parsed = _llm_json(llm, system=system, user=user, max_tokens=4000)
    if not parsed:
        return RoleOutput("task_builder", "failed", "task author returned no parseable JSON")
    data = {
        "task_title": str(parsed.get("task_title") or "").strip(),
        "task_summary": str(parsed.get("task_summary") or "").strip(),
        "impl_path": str(parsed.get("impl_path") or "").replace("\\", "/").strip(),
        "test_path": str(parsed.get("test_path") or "").replace("\\", "/").strip(),
        "test_content": parsed.get("test_content") or "",
    }
    try:
        data["confidence"] = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        data["confidence"] = 0.0
    if not data["task_title"] or not isinstance(data["test_content"], str):
        return RoleOutput("task_builder", "failed", "task author reply missing fields", data)
    return RoleOutput(
        "task_builder",
        "built",
        f"proposed task {data['task_title']!r} with test {data['test_path']}",
        data,
    )


# ── the task critic (anti-garbage) ──────────────────────────────────────────


_MEANINGLESS_ASSERTS = ("assert true", "assert 1", "assert 1 == 1", "assert not false")


def _module_import_stem(impl_path: str) -> str:
    """``core/foo.py`` -> ``foo`` — the importable stem tests should reference."""
    rel = impl_path.replace("\\", "/").strip()
    if rel.endswith(".py"):
        rel = rel[:-3]
    return rel.rsplit("/", 1)[-1]


def _test_references_module(test_content: str, impl_path: str) -> bool:
    """True when the test imports/uses the implementation module by name."""
    stem = _module_import_stem(impl_path)
    if not stem:
        return False
    low = test_content
    # Accept ``from core.foo import ...``, ``import core.foo``, or ``core.foo``.
    dotted = impl_path.replace("\\", "/")
    if dotted.endswith(".py"):
        dotted = dotted[:-3]
    dotted = dotted.replace("/", ".")
    return dotted in low or re.search(rf"\b{re.escape(stem)}\b", low) is not None


def _has_meaningful_assert(test_content: str) -> bool:
    lines = [ln.strip().lower() for ln in test_content.splitlines()]
    asserts = [ln for ln in lines if ln.startswith("assert ") or ln.startswith("assert(")]
    if not asserts:
        # pytest.raises / self.assert* also count as real assertions.
        low = test_content.lower()
        return "pytest.raises" in low or "assert" in low and "def test" in low
    meaningful = [a for a in asserts if not any(a.startswith(m) for m in _MEANINGLESS_ASSERTS)]
    return bool(meaningful)


def _task_critic_review(
    build: dict[str, Any],
    *,
    grounded_target: str,
    reader: Callable[[str], str | None],
    confidence_threshold: float,
) -> RoleOutput:
    """Reject garbage tasks BEFORE a human ever sees them. Any failure vetoes."""
    veto: list[str] = []
    impl_path = str(build.get("impl_path") or "").replace("\\", "/").strip()
    test_path = str(build.get("test_path") or "").replace("\\", "/").strip()
    test_content = build.get("test_content") or ""
    confidence = float(build.get("confidence") or 0.0)

    norm_target = grounded_target.replace("\\", "/").strip()
    if not impl_path:
        veto.append("no implementation path")
    elif impl_path != norm_target:
        veto.append(
            f"impl_path {impl_path!r} does not match grounded target {norm_target!r}"
        )
    elif not _is_self_build_target_allowed(impl_path):
        veto.append(f"impl_path {impl_path!r} is not a low-risk editable file")

    if not test_path:
        veto.append("no test path")
    else:
        if not test_path.startswith("tests/"):
            veto.append(f"test_path {test_path!r} must live under tests/")
        if not test_path.endswith(".py"):
            veto.append(f"test_path {test_path!r} is not a Python file")
        if reader(test_path) is not None:
            veto.append(f"test_path {test_path!r} already exists (must be a new test)")

    if not isinstance(test_content, str) or not test_content.strip():
        veto.append("empty test content")
    else:
        if _looks_like_diff(test_content):
            veto.append("test content looks like a diff, not a full file")
        if len(test_content.encode("utf-8")) > _MAX_CONTENT_BYTES:
            veto.append("test content is too large")
        try:
            ast.parse(test_content)
        except SyntaxError as exc:
            veto.append(f"test does not parse: {exc.msg}")
        if "def test" not in test_content:
            veto.append("test content defines no test function")
        if "[REDACTED:" in test_content:
            veto.append("test content contains redaction markers")
        if impl_path and not _test_references_module(test_content, impl_path):
            veto.append("test does not reference the implementation module")
        if not _has_meaningful_assert(test_content):
            veto.append("test has no meaningful assertion")

    if not build.get("task_title"):
        veto.append("task has no title")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    decision = "veto" if veto else "pass"
    return RoleOutput(
        "task_critic",
        decision,
        "; ".join(veto) if veto else "all task-critic checks passed",
        {"veto_reasons": veto, "confidence": confidence},
    )


# ── the reporter ────────────────────────────────────────────────────────────


def _task_reporter_publish(
    inbox: Any, build: dict[str, Any], evidence: list[str], evidence_ref: str
) -> RoleOutput:
    """Create exactly one Stage-A approval item (inert until Stage B)."""
    impl_path = build["impl_path"]
    test_path = build["test_path"]
    test_content = build["test_content"]
    # The durable inbox runs every payload through the DLP/secret redactor
    # (approval_inbox._redact_durable_payload). A frozen acceptance test is
    # SOURCE CODE that must survive byte-for-byte — but it legitimately contains
    # example PII (e.g. "alice@mail.ru") that the redactor would scrub, breaking
    # the test. We therefore persist an exact base64 copy (redaction-inert: no
    # "@"/email/token patterns) alongside the human-readable preview. Stage B
    # reads the exact test via ``decode_frozen_test``; the raw ``test_content``
    # field is only a (possibly redacted) preview for humans.
    test_content_b64 = base64.b64encode(test_content.encode("utf-8")).decode("ascii")
    payload = {
        "task_title": build.get("task_title") or "",
        "task_summary": build.get("task_summary") or "",
        "impl_path": impl_path,
        "test_path": test_path,
        "test_content": test_content,
        "test_content_b64": test_content_b64,
        "evidence": list(evidence),
        "evidence_ref": evidence_ref,
        "confidence": float(build.get("confidence") or 0.0),
        "origin": TASK_PRODUCER_ORIGIN,
    }
    digest = hashlib.sha1(
        (impl_path + "\n" + test_content).encode("utf-8")
    ).hexdigest()[:12]
    dedup_key = f"self_task:{impl_path}:{digest}"
    summary = f"coding task: {build.get('task_title') or impl_path} → {impl_path}"
    item = inbox.add(
        operation=SELF_TASK_OPERATION,
        summary=summary,
        risk="reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=dedup_key,
    )
    return RoleOutput(
        "reporter",
        "published",
        f"created task approval item {item.id}",
        {"approval_id": item.id, "dedup_key": dedup_key},
    )


# ── orchestration ───────────────────────────────────────────────────────────


def produce_coding_task(
    *,
    workspace: str | Path,
    inbox: Any,
    llm: Any,
    vcs: Any = None,
    budget_snapshot: Any = None,
    kill_switch: Any = None,
    file_reader: Callable[[str], str | None] | None = None,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    task_selector: Callable[[], Any] | None = None,
) -> ProducerReport:
    """Stage A: publish at most one grounded coding-task proposal for approval.

    Never writes code, never applies a patch, creates at most one inbox item.
    Returns a :class:`ProducerReport` whose ``status`` is one of:
    ``budget_kill_switch`` / ``budget_wait`` / ``task_wait`` / ``dirty_tree_wait``
    / ``no_task`` / ``task_veto`` / ``proposed``.
    """
    gates: list[str] = []

    # ── gate 1: budget kill-switch ──────────────────────────────────────────
    if kill_switch is not None and getattr(kill_switch, "active", False):
        return ProducerReport(
            status="budget_kill_switch",
            reason=str(getattr(kill_switch, "reason", "") or "kill-switch active"),
            checked_gates=["kill_switch"],
            next_human_action="Resolve budget kill-switch before proposing a task.",
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

    # ── gate 3: a Stage-A task already waits for a human ─────────────────────
    if _has_pending_task(inbox):
        return ProducerReport(
            status="task_wait",
            reason="a pending self_build_task.approve item already exists",
            checked_gates=gates + ["task"],
            next_human_action="Resolve the existing task approval first.",
        )
    gates.append("task")

    # ── gate 4: dirty working tree ──────────────────────────────────────────
    if vcs is not None and not vcs.is_clean():
        return ProducerReport(
            status="dirty_tree_wait",
            reason="git working tree is not clean",
            checked_gates=gates + ["dirty_tree"],
            next_human_action="Commit or stash local changes, then retry.",
        )
    gates.append("dirty_tree")

    reader = file_reader or _default_file_reader(workspace)
    roles: list[RoleOutput] = []

    # ── selector: one grounded code_todo candidate ──────────────────────────
    selector = task_selector or _default_task_selector(workspace)
    try:
        candidate = selector()
    except Exception:  # noqa: BLE001
        candidate = None
    if candidate is None:
        return ProducerReport(
            status="no_task",
            reason="no grounded code TODO/FIXME candidate available",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No grounded coding-task candidate this run.",
        )
    impl_path = _field(candidate, "target_path").replace("\\", "/")
    quote = _field(candidate, "problem_quote")
    evidence_ref = _field(candidate, "evidence_ref")
    if not _is_self_build_target_allowed(impl_path):
        return ProducerReport(
            status="no_task",
            reason=f"grounded target {impl_path!r} is not a low-risk editable file",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No grounded coding-task candidate this run.",
        )
    roles.append(
        RoleOutput(
            "selector",
            "selected",
            f"selected {impl_path} from {evidence_ref}",
            {"impl_path": impl_path, "evidence_ref": evidence_ref, "quote": quote},
        )
    )

    current_content = reader(impl_path) or ""
    evidence = [f"code_todo: {evidence_ref}", f"TODO: {quote}"]

    # ── task builder ────────────────────────────────────────────────────────
    builder = _task_builder_generate(
        llm,
        impl_path=impl_path,
        quote=quote,
        evidence_ref=evidence_ref,
        current_content=current_content,
    )
    roles.append(builder)
    if builder.decision != "built":
        return ProducerReport(
            status="task_veto",
            reason=builder.detail or "task author failed",
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=[builder.detail or "task author failed"],
            next_human_action="Task author failed; no approval created.",
        )

    # ── task critic ─────────────────────────────────────────────────────────
    critic = _task_critic_review(
        builder.data,
        grounded_target=impl_path,
        reader=reader,
        confidence_threshold=confidence_threshold,
    )
    roles.append(critic)
    if critic.decision == "veto":
        return ProducerReport(
            status="task_veto",
            reason=critic.detail,
            target_path=impl_path,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(critic.data.get("veto_reasons", [])),
            next_human_action="Task critic vetoed the candidate; no approval created.",
        )

    # ── reporter ────────────────────────────────────────────────────────────
    reporter = _task_reporter_publish(inbox, builder.data, evidence, evidence_ref)
    roles.append(reporter)
    approval_id = reporter.data["approval_id"]
    return ProducerReport(
        status="proposed",
        reason=builder.data.get("task_summary") or builder.data.get("task_title") or "",
        target_path=impl_path,
        approval_id=approval_id,
        checked_gates=gates,
        role_outputs=roles,
        next_human_action=(
            f"Review task {approval_id} (read the test!), approve with "
            f":approval-approve {approval_id}, then build it with "
            f":self-task-build {approval_id}."
        ),
    )
