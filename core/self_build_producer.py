"""Subagent-backed full self-apply proposal producer (TD-025).

This closes the *producer* gap in the self-build loop. The self-apply lane
(TD-023) and the approval bridge (TD-024) can already apply an approved
full-content proposal, but nothing yet *generates* a valid
``operation="self_apply_lane.run"`` payload autonomously. The advisory
self-build supervisor only emits a unified diff / ``NO_PATCH``.

This module adds a narrow producer that runs a small, explicit role pipeline —

    Manager -> Researcher -> Builder -> Critic -> Reporter

— to produce **at most one** validated low-risk self-apply proposal with full
file content and place it into the approval inbox. It never applies the patch,
never runs the lane, never commits/pushes/merges, never touches the daemon,
scheduler or agent_tick, and never changes budget/model/catalog config.

Hard safety gates (checked before any LLM-heavy work, first trip wins):
  1. budget kill-switch active     -> status="budget_kill_switch"
  2. hour budget near-exhaustion   -> status="budget_wait"
  3. pending self_apply approval    -> status="approval_wait"
  4. dirty git working tree        -> status="dirty_tree_wait"

Then the roles run. The Manager may pick no candidate (``no_patch``); the
Critic may veto (``critic_veto``, no inbox item created); otherwise exactly one
approval inbox item is created (``proposed``).

Every dependency (LLM, VCS, inbox, budget snapshot, kill-switch, file reader)
is injected so the whole pipeline is unit-testable with fakes — no real
provider/network/LLM call ever happens here.
"""
from __future__ import annotations

import ast
import hashlib
import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from core.self_apply_bridge import (
    DEFAULT_ROLLBACK,
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
)
from core.self_apply_lane import FileChange, classify_patch_risk
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# Origin tag stamped on every payload this producer emits, so downstream tooling
# (and the TD-024 bridge) can tell an autonomously-produced proposal apart from
# a manual or repair-produced one.
PRODUCER_ORIGIN = "subagent_self_build_producer"

# Narrow, hardcoded allowlist of small low-risk candidate targets for the first
# PR. The Manager may only pick from this list; it is NOT allowed to freely scan
# or choose across the whole repository yet (that is a later, daemon-wired step).
DEFAULT_CANDIDATE_TARGETS: tuple[str, ...] = (
    "core/redaction.py",
    "core/truth_hype_filter.py",
    "docs/self_build.md",
)

# Critical "organs" that must never be a producer target in this first PR, even
# though some would otherwise pass the lane allowlist. Denylist wins before the
# allowlist. Prefix entries (ending in "/") match any file beneath them.
CRITICAL_DENY: tuple[str, ...] = (
    "main.py",
    "core/loop.py",
    "core/autonomous_runtime.py",
    "core/model_usage.py",
    "core/safe_vcs.py",
    "core/self_apply_lane.py",
    "core/self_apply_bridge.py",
    "core/self_build_producer.py",
    "config/",
)

# Upper bound on generated file size. A low-risk self-build patch is small; a
# huge blob is a signal of a runaway generation, not a surgical change.
_MAX_CONTENT_BYTES = 60_000

_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# Lines/markers that betray a diff/patch instead of full file content. The
# Builder must return the whole post-image; a diff-only answer is vetoed.
_DIFF_MARKERS = ("--- ", "+++ ", "@@ ", "diff --git", "index ")


# ── data ────────────────────────────────────────────────────────────────────


@dataclass
class RoleOutput:
    """One structured role result, surfaced verbatim in the ProducerReport so an
    operator can see *who decided what*."""

    role: str
    decision: str
    detail: str = ""
    data: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "decision": self.decision,
            "detail": self.detail,
            "data": dict(self.data),
        }


@dataclass
class ProducerReport:
    status: str
    reason: str = ""
    target_path: str | None = None
    approval_id: str | None = None
    checked_gates: list[str] = field(default_factory=list)
    role_outputs: list[RoleOutput] = field(default_factory=list)
    veto_reasons: list[str] = field(default_factory=list)
    next_human_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "target_path": self.target_path,
            "approval_id": self.approval_id,
            "checked_gates": list(self.checked_gates),
            "roles": [r.to_dict() for r in self.role_outputs],
            "veto_reasons": list(self.veto_reasons),
            "next_human_action": self.next_human_action,
        }


# ── helpers ─────────────────────────────────────────────────────────────────


def _parse_json(text: str) -> dict | None:
    """Best-effort structured parse of an LLM answer.

    Accepts clean JSON or JSON inside a ```json fenced block. Returns None when
    nothing parseable is found — the caller then treats it as a role failure,
    never as a silent success.
    """
    if not isinstance(text, str) or not text.strip():
        return None
    candidate = text.strip()
    fence = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", candidate, re.DOTALL)
    if fence:
        candidate = fence.group(1)
    else:
        first = candidate.find("{")
        last = candidate.rfind("}")
        if first != -1 and last != -1 and last > first:
            candidate = candidate[first : last + 1]
    try:
        parsed = json.loads(candidate)
    except (json.JSONDecodeError, ValueError):
        return None
    return parsed if isinstance(parsed, dict) else None


def _looks_like_diff(content: str) -> bool:
    for line in content.splitlines():
        stripped = line.lstrip()
        if any(stripped.startswith(marker) for marker in _DIFF_MARKERS):
            return True
    return False


def _is_critical(rel: str) -> bool:
    lower = rel.replace("\\", "/").strip().lower()
    for deny in CRITICAL_DENY:
        d = deny.lower()
        if d.endswith("/"):
            if lower.startswith(d):
                return True
        elif lower == d:
            return True
    return False


def _default_file_reader(workspace: str | Path) -> Callable[[str], str | None]:
    root = Path(workspace).resolve()

    def read(path: str) -> str | None:
        target = (root / path).resolve()
        if root not in target.parents and target != root:
            return None
        try:
            return target.read_text(encoding="utf-8")
        except (FileNotFoundError, OSError, UnicodeDecodeError):
            return None

    return read


def _llm_json(llm: Any, *, system: str, user: str) -> dict | None:
    try:
        answer = llm.complete(
            system=system, user=user, max_tokens=2000, temperature=0.0
        )
    except TypeError:
        # Tolerate positional-only fakes.
        answer = llm.complete(system, user)
    return _parse_json(answer if isinstance(answer, str) else "")


# ── roles ───────────────────────────────────────────────────────────────────


def _manager_select(
    llm: Any, candidate_targets: tuple[str, ...]
) -> RoleOutput:
    """Choose exactly one small low-risk target from the hardcoded allowlist."""
    listing = "\n".join(f"- {t}" for t in candidate_targets)
    system = (
        "You are the Manager of a self-build team. Choose exactly ONE small, "
        "low-risk file to improve from the provided allowlist. You may not pick "
        "anything outside the list. Reply with strict JSON only: "
        '{"target": "<path or null>", "diagnosis": "<one sentence>"}.'
    )
    user = f"Allowlisted candidate targets:\n{listing}\n\nPick one or null."
    parsed = _llm_json(llm, system=system, user=user) or {}
    target = parsed.get("target")
    diagnosis = str(parsed.get("diagnosis") or "").strip()
    if not isinstance(target, str) or not target.strip():
        return RoleOutput("manager", "no_target", "manager selected no target")
    target = target.strip()
    if target not in candidate_targets:
        return RoleOutput(
            "manager",
            "no_target",
            f"manager picked off-allowlist target {target!r}",
            {"rejected_target": target},
        )
    if _is_critical(target):
        return RoleOutput(
            "manager",
            "no_target",
            f"manager picked critical target {target!r}",
            {"rejected_target": target},
        )
    return RoleOutput(
        "manager",
        "selected",
        f"selected {target}",
        {"target": target, "diagnosis": diagnosis},
    )


def _researcher_gather(
    file_reader: Callable[[str], str | None], target: str, diagnosis: str
) -> RoleOutput:
    """Read current file content (read-only) and assemble evidence."""
    current = file_reader(target)
    exists = current is not None
    current = current or ""
    line_count = len(current.splitlines())
    evidence = [
        f"target={target}",
        f"exists={exists}",
        f"current_lines={line_count}",
    ]
    if diagnosis:
        evidence.append(f"diagnosis={diagnosis}")
    return RoleOutput(
        "researcher",
        "gathered",
        f"read {target} ({line_count} lines, exists={exists})",
        {"current_content": current, "exists": exists, "evidence": evidence},
    )


def _builder_generate(
    llm: Any, target: str, current_content: str, diagnosis: str
) -> RoleOutput:
    """Generate the FULL proposed file content (never a diff)."""
    system = (
        "You are the Builder. Produce the COMPLETE new content of the target "
        "file — the entire file, not a diff or patch. Keep the change small and "
        "low-risk. Reply with strict JSON only: "
        '{"content": "<full file content>", "test_paths": ["tests"], '
        '"test_pattern": "<optional pytest -k pattern or null>", '
        '"reason": "<one sentence>", "confidence": <0..1>}.'
    )
    user = (
        f"Target file: {target}\n"
        f"Diagnosis: {diagnosis or '(none)'}\n\n"
        f"Current content:\n{current_content or '(empty / new file)'}"
    )
    parsed = _llm_json(llm, system=system, user=user)
    if not parsed:
        return RoleOutput(
            "builder", "failed", "builder returned no parseable JSON"
        )
    content = parsed.get("content")
    if not isinstance(content, str):
        content = ""
    test_paths = parsed.get("test_paths") or ["tests"]
    if not isinstance(test_paths, (list, tuple)) or not test_paths:
        test_paths = ["tests"]
    test_pattern = parsed.get("test_pattern")
    if not isinstance(test_pattern, str) or not test_pattern.strip():
        test_pattern = None
    reason = str(parsed.get("reason") or diagnosis or "").strip()
    try:
        confidence = float(parsed.get("confidence", 0.0))
    except (TypeError, ValueError):
        confidence = 0.0
    return RoleOutput(
        "builder",
        "built" if content else "failed",
        f"generated {len(content)} chars (confidence {confidence:.2f})",
        {
            "content": content,
            "test_paths": [str(p) for p in test_paths],
            "test_pattern": test_pattern,
            "reason": reason,
            "confidence": confidence,
        },
    )


def _critic_review(
    target: str,
    current_content: str,
    build: dict[str, Any],
    *,
    confidence_threshold: float,
) -> RoleOutput:
    """Validate the built content; any failing check is a hard veto."""
    veto: list[str] = []
    content = build.get("content") or ""
    confidence = float(build.get("confidence") or 0.0)

    if not isinstance(content, str) or not content.strip():
        veto.append("empty generated content")
    if _is_critical(target):
        veto.append(f"target {target} is a critical file")
    if content and _looks_like_diff(content):
        veto.append("generated content looks like a diff, not full content")
    if content and content == current_content:
        veto.append("generated content is identical to current file")
    if len(content.encode("utf-8")) > _MAX_CONTENT_BYTES:
        veto.append(
            f"generated content exceeds {_MAX_CONTENT_BYTES} bytes"
        )
    if not build.get("test_paths"):
        veto.append("no targeted tests specified")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    ok, risk_reason, rejected = classify_patch_risk(
        [FileChange(path=target, content=content)]
    )
    if not ok:
        veto.append(f"risk classification failed: {risk_reason}")

    if target.lower().endswith(".py") and content and not _looks_like_diff(content):
        try:
            ast.parse(content)
        except SyntaxError as exc:
            veto.append(f"generated python does not parse: {exc.msg}")

    decision = "veto" if veto else "pass"
    return RoleOutput(
        "critic",
        decision,
        "; ".join(veto) if veto else "all critic checks passed",
        {
            "veto_reasons": veto,
            "confidence": confidence,
            "risk_ok": ok,
            "rejected_files": rejected,
        },
    )


def _reporter_publish(
    inbox: Any,
    target: str,
    build: dict[str, Any],
    evidence: list[str],
) -> RoleOutput:
    """Create exactly one approval inbox item for the TD-024 bridge."""
    content = build["content"]
    payload = build_self_apply_payload(
        files=[{"path": target, "content": content}],
        reason=build.get("reason") or "",
        evidence=evidence,
        test_paths=build.get("test_paths") or ["tests"],
        test_pattern=build.get("test_pattern"),
        origin=PRODUCER_ORIGIN,
        rollback=DEFAULT_ROLLBACK,
    )
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    dedup_key = f"self_apply:{target}:{digest}"
    item = inbox.add(
        operation=SELF_APPLY_OPERATION,
        summary=f"self-apply proposal for {target}",
        risk="reversible",
        reasons=tuple(evidence),
        payload=payload,
        dedup_key=dedup_key,
    )
    return RoleOutput(
        "reporter",
        "published",
        f"created approval item {item.id}",
        {"approval_id": item.id, "dedup_key": dedup_key},
    )


# ── orchestration ───────────────────────────────────────────────────────────


def _has_pending_self_apply(inbox: Any) -> bool:
    for item in inbox.list():
        if getattr(item, "operation", "") != SELF_APPLY_OPERATION:
            continue
        if getattr(item, "status", "") in ("pending", "approved"):
            return True
    return False


def produce_self_apply_proposal(
    *,
    workspace: str | Path,
    inbox: Any,
    llm: Any,
    vcs: Any = None,
    budget_snapshot: Any = None,
    kill_switch: Any = None,
    file_reader: Callable[[str], str | None] | None = None,
    candidate_targets: tuple[str, ...] | list[str] | None = None,
    confidence_threshold: float = _DEFAULT_CONFIDENCE_THRESHOLD,
    now_iso: str | None = None,
    registry: Any = None,
) -> ProducerReport:
    """Run the Manager/Researcher/Builder/Critic/Reporter pipeline to produce at
    most one validated low-risk self-apply proposal into the approval inbox.

    Returns a :class:`ProducerReport`. The function never applies the patch,
    never runs the lane, and creates at most one inbox item per call.

    If ``registry`` (a ``SubagentRegistry``) is provided, the run's role outcomes
    are recorded into it as an additive, best-effort side effect (TD-028). When
    ``registry is None`` — the default — behaviour is byte-identical to before,
    and a registry write failure can never change or break this function.
    """
    def _record(report: ProducerReport) -> ProducerReport:
        if registry is not None:
            try:
                registry.record_report(report)
            except Exception:  # noqa: BLE001 — recording must never break producer
                pass
        return report

    targets = tuple(candidate_targets) if candidate_targets else DEFAULT_CANDIDATE_TARGETS
    gates: list[str] = []

    # ── gate 1: budget kill-switch (before ANY LLM-heavy work) ──────────────
    if kill_switch is not None and getattr(kill_switch, "active", False):
        return _record(ProducerReport(
            status="budget_kill_switch",
            reason=str(getattr(kill_switch, "reason", "") or "kill-switch active"),
            checked_gates=["kill_switch"],
            next_human_action="Resolve budget kill-switch before self-build.",
        ))
    gates.append("kill_switch")

    # ── gate 2: hour budget near-exhaustion ─────────────────────────────────
    if budget_snapshot is not None:
        headroom = hour_budget_headroom(budget_snapshot)
        near, reasons = is_budget_near_exhaustion(headroom)
        if near:
            return _record(ProducerReport(
                status="budget_wait",
                reason="; ".join(reasons) or "budget near exhaustion",
                checked_gates=gates + ["budget"],
                next_human_action="Wait for the budget window to refill.",
            ))
    gates.append("budget")

    # ── gate 3: an unexecuted self_apply approval already exists ────────────
    if _has_pending_self_apply(inbox):
        return _record(ProducerReport(
            status="approval_wait",
            reason="a pending self_apply_lane.run approval item already exists",
            checked_gates=gates + ["approval"],
            next_human_action="Resolve the existing self-apply approval first.",
        ))
    gates.append("approval")

    # ── gate 4: dirty working tree ──────────────────────────────────────────
    if vcs is not None and not vcs.is_clean():
        return _record(ProducerReport(
            status="dirty_tree_wait",
            reason="git working tree is not clean",
            checked_gates=gates + ["dirty_tree"],
            next_human_action="Commit or stash local changes, then retry.",
        ))
    gates.append("dirty_tree")

    reader = file_reader or _default_file_reader(workspace)
    roles: list[RoleOutput] = []

    # ── Manager ─────────────────────────────────────────────────────────────
    manager = _manager_select(llm, targets)
    roles.append(manager)
    if manager.decision != "selected":
        return _record(ProducerReport(
            status="no_patch",
            reason=manager.detail or "no low-risk candidate selected",
            checked_gates=gates,
            role_outputs=roles,
            next_human_action="No low-risk self-build candidate this run.",
        ))
    target = manager.data["target"]
    diagnosis = manager.data.get("diagnosis", "")

    # ── Researcher ──────────────────────────────────────────────────────────
    researcher = _researcher_gather(reader, target, diagnosis)
    roles.append(researcher)
    current_content = researcher.data["current_content"]
    evidence = list(researcher.data["evidence"])

    # ── Builder ─────────────────────────────────────────────────────────────
    builder = _builder_generate(llm, target, current_content, diagnosis)
    roles.append(builder)

    # ── Critic ──────────────────────────────────────────────────────────────
    critic = _critic_review(
        target,
        current_content,
        builder.data,
        confidence_threshold=confidence_threshold,
    )
    roles.append(critic)
    if critic.decision == "veto":
        return _record(ProducerReport(
            status="critic_veto",
            reason=critic.detail,
            target_path=target,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(critic.data.get("veto_reasons", [])),
            next_human_action="Critic vetoed the candidate; no approval created.",
        ))

    # ── Reporter ────────────────────────────────────────────────────────────
    reporter = _reporter_publish(inbox, target, builder.data, evidence)
    roles.append(reporter)
    approval_id = reporter.data["approval_id"]
    return _record(ProducerReport(
        status="proposed",
        reason=builder.data.get("reason") or diagnosis,
        target_path=target,
        approval_id=approval_id,
        checked_gates=gates,
        role_outputs=roles,
        next_human_action=(
            f"Review approval item {approval_id} and run "
            f":self-apply-run {approval_id} to apply it."
        ),
    ))
