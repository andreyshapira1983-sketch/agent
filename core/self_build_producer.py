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

from core.backlog_target_mapper import map_backlog_candidate
from core.injection_guard import prepare_untrusted_text_for_llm
from core.redaction import prepare_text_for_llm_boundary
from core.self_apply_bridge import (
    DEFAULT_ROLLBACK,
    SELF_APPLY_OPERATION,
    build_self_apply_payload,
)
from core.self_apply_lane import FileChange, _normalize_rel, classify_patch_risk
from core.proposal_value_gate import evaluate_proposal_value
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# Origin tag stamped on every payload this producer emits, so downstream tooling
# (and the TD-024 bridge) can tell an autonomously-produced proposal apart from
# a manual or repair-produced one.
PRODUCER_ORIGIN = "subagent_self_build_producer"

# Seed allowlist of small low-risk candidate targets. Historically this was the
# ONLY set the Manager could pick from. As of Provod #2 it is just a seed: a
# grounded backlog candidate may also target any file that clears both hard
# safety layers (see ``_is_self_build_target_allowed``) — the critical-organ
# denylist below and the self-apply lane's low-risk classifier. Apply authority
# is unchanged: every patch is still dry-run + human-approved + test-gated.
DEFAULT_CANDIDATE_TARGETS: tuple[str, ...] = (
    "core/redaction.py",
    "core/truth_hype_filter.py",
    "docs/self_build.md",
)
_SELF_BUILD_DOC_TARGET = "docs/self_build.md"

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

# The Builder must return the FULL post-image of the target file, not a diff. A
# 2000-token cap truncates any non-trivial file (the JSON is cut off and fails to
# parse, which the Critic then vetoes as "empty generated content"). This gives
# enough headroom for real files while staying under _MAX_CONTENT_BYTES.
_BUILDER_MAX_TOKENS = 16_000

_DEFAULT_CONFIDENCE_THRESHOLD = 0.6

# Lines/markers that betray a diff/patch instead of full file content. The
# Builder must return the whole post-image; a diff-only answer is vetoed.
_DIFF_MARKERS = ("--- ", "+++ ", "@@ ", "diff --git", "index ")

_SELF_BUILD_DOC_REQUIRED_TOPICS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("self-build produce command", (":self-build-produce",)),
    ("approval item only", ("approval item", "approval inbox")),
    ("human approval inspection", (":approval-list", ":approval-triage", "inspect")),
    (
        "approve or deny decision",
        (":approval-approve", ":approval-deny", "approve", "deny"),
    ),
    ("separate self-apply run", (":self-apply-run", "separate")),
    ("no auto-apply", ("no auto-apply",)),
    ("no auto-commit", ("no auto-commit",)),
    ("scheduler and allow-effects boundary", ("scheduler", "allow-effects")),
    ("blocked autonomy tracks", ("g6a", "gateway", "runner", "remote git")),
    ("safe operator checklist", ("checklist",)),
    ("reject criteria", ("reject", "deny if")),
)

_SELF_BUILD_DOC_GUIDANCE = (
    "For docs/self_build.md specifically, write an operator guide for this "
    "repo's human-gated self-build loop, not a generic build-from-source or "
    "installation guide. It must explain: :self-build-produce creates one "
    "approval item only; the human inspects approval items with :approval-list "
    "or :approval-triage; the human approves or denies with :approval-approve "
    "or :approval-deny; :self-apply-run is separate and requires explicit "
    "human approval; no auto-apply; no auto-commit; no scheduler or "
    "allow-effects; no G6a, gateway changes, runner work, or remote git; a "
    "safe operator checklist; and reject criteria for bad proposals."
)


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
    value_flags: list[str] = field(default_factory=list)
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
            "value_flags": list(self.value_flags),
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


def _is_self_build_doc_target(target: str) -> bool:
    return target.replace("\\", "/").strip().lower() == _SELF_BUILD_DOC_TARGET


def _missing_self_build_doc_topics(content: str) -> list[str]:
    low = (content or "").lower()
    missing: list[str] = []
    # Each topic's markers are ALTERNATIVES: any one satisfies the topic. This
    # matches how _SELF_BUILD_DOC_GUIDANCE phrases them ("or") and prevents a
    # Builder-following operator guide from being vetoed for not containing every
    # synonym (e.g. both :approval-list and :approval-triage, or a literal
    # "deny if").
    for label, markers in _SELF_BUILD_DOC_REQUIRED_TOPICS:
        if not any(marker in low for marker in markers):
            missing.append(label)
    return missing


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


def _is_self_build_target_allowed(target: str) -> bool:
    """Provod #2 (bold-mode) acceptance policy for a grounded backlog target.

    Instead of the narrow hardcoded :data:`DEFAULT_CANDIDATE_TARGETS` trio, a
    grounded target is now accepted when it clears BOTH hard safety layers that
    already exist:

    * it is not a critical "organ" (:data:`CRITICAL_DENY` — main.py, loop.py,
      autonomous_runtime.py, safe_vcs.py, the self-apply/self-build machinery,
      config/, ...); and
    * the self-apply lane's own low-risk classifier would accept it
      (``core``/``cli``/``tools``/``tests`` ``*.py``, anything under ``docs/``,
      any ``*.md``; never ``.github``/``config``/``secrets``/lockfiles/keys).

    This only widens *candidate discovery* so the agent can act on its own
    architecture-audit findings. It does NOT widen apply authority: every
    produced patch is still dry-run, placed in the approval inbox for a human to
    approve, and applied only via the lane (which re-runs tests and auto-rolls
    back on red). Pure/deterministic; never raises.
    """
    rel = str(target or "").replace("\\", "/").strip()
    if not rel:
        return False
    # Canonicalize exactly the way the lane classifier does (drop ``.`` segments,
    # reject ``..``/absolute/drive paths) BEFORE the critical-organ check. Without
    # this, an alias like ``./core/loop.py`` or ``core/./loop.py`` slips past
    # _is_critical (raw-string match) yet classify_patch_risk normalizes it to the
    # critical ``core/loop.py`` and accepts it — admitting a critical organ the
    # fixed allowlist blocked. A path _normalize_rel rejects is never low-risk.
    canonical = _normalize_rel(rel)
    if canonical is None or _is_critical(canonical):
        return False
    ok, _reason, _rejected = classify_patch_risk(
        [FileChange(path=canonical, content="pass\n")]
    )
    return bool(ok)


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


def _llm_json(
    llm: Any, *, system: str, user: str, max_tokens: int = 2000
) -> dict | None:
    safe_user, _redact_meta = prepare_text_for_llm_boundary(user)
    try:
        answer = llm.complete(
            system=system, user=safe_user, max_tokens=max_tokens, temperature=0.0
        )
    except TypeError:
        # Tolerate positional-only fakes.
        answer = llm.complete(system, safe_user)
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


def _manager_from_grounded(
    provider: Callable[[], Any],
    candidate_targets: tuple[str, ...],
    *,
    workspace: str | Path,
) -> RoleOutput:
    """Manager variant (TD-036) that takes its target + diagnosis from a grounded
    backlog candidate instead of inventing one via the LLM.

    ``provider`` is a zero-arg callable returning a candidate (with
    ``target_path``/``problem_quote``/``evidence_ref``) or ``None``. Before
    validation, TD-038 gives known abstract targets one deterministic mapping
    attempt. A ``None`` candidate, an unmapped/off-allowlist target, or a
    critical target all yield ``no_target`` — the producer then refuses rather
    than fabricating a diagnosis. Best-effort: a raising provider is treated as
    ``None``.
    """
    try:
        candidate = provider()
    except Exception:  # noqa: BLE001 — a broken selector must never break producer
        candidate = None
    if candidate is None:
        return RoleOutput("manager", "no_target", "no grounded backlog candidate")

    # Provod #2: widen the effective allowlist beyond the hardcoded trio to any
    # grounded target that clears BOTH hard safety layers (critical-organ deny +
    # the self-apply lane's low-risk classifier). Apply authority is unchanged —
    # every produced patch still needs human approval and passing tests.
    candidate_target = str(getattr(candidate, "target_path", "") or "").replace(
        "\\", "/"
    ).strip()
    effective_allowed = set(candidate_targets)
    if candidate_target and _is_self_build_target_allowed(candidate_target):
        effective_allowed.add(candidate_target)

    mapping = map_backlog_candidate(
        candidate,
        workspace=workspace,
        allowed_targets=effective_allowed,
    )
    if not mapping.ok or mapping.candidate is None:
        rejected = str(getattr(candidate, "target_path", "") or "").strip()
        data = {
            "rejected_target": rejected,
            "mapping_decision": mapping.decision,
        }
        if mapping.mapping_rule:
            data["mapping_rule"] = mapping.mapping_rule
        return RoleOutput("manager", "no_target", mapping.reason, data)

    mapped_candidate = mapping.candidate
    target = mapped_candidate.target_path
    diagnosis = mapped_candidate.problem_quote
    evidence_ref = mapped_candidate.evidence_ref
    if not target:
        return RoleOutput("manager", "no_target", "grounded candidate has no target")
    if mapping.decision != "mapped" and target not in effective_allowed:
        return RoleOutput(
            "manager",
            "no_target",
            f"grounded target {target!r} is off-allowlist",
            {"rejected_target": target},
        )
    if _is_critical(target):
        return RoleOutput(
            "manager",
            "no_target",
            f"grounded target {target!r} is critical",
            {"rejected_target": target},
        )
    return RoleOutput(
        "manager",
        "selected",
        f"selected {target} (grounded)",
        {
            "target": target,
            "diagnosis": diagnosis,
            "grounded": True,
            "evidence_ref": evidence_ref,
            "mapping_decision": mapping.decision,
            "mapping_rule": mapped_candidate.mapping_rule,
            "source_target_path": mapped_candidate.source_target_path,
            "proposed_change": mapped_candidate.proposed_change,
            "proof_of_value": mapped_candidate.proof_of_value,
            "expected_effect": mapped_candidate.expected_effect,
            "confidence": mapped_candidate.confidence,
        },
    )


def _grounded_candidate_actionable(candidate: Any, workspace: str | Path) -> bool:
    """True if the producer could actually act on this ranked candidate.

    Either its raw target is directly low-risk and non-critical, OR the
    deterministic mapper resolves its abstract target (e.g. ``TD-011 / TD-012``)
    to an allowed, non-critical concrete file. Checking BOTH is essential: a
    tech-debt item's raw target is an abstract id that fails the low-risk file
    check, yet it is genuinely actionable once mapped — so a raw-only check would
    wrongly skip a high-ranked tech-debt item in favour of a lower-ranked audit
    item, breaking tech-debt-first ranking. Best-effort: a raising mapper is
    treated as not actionable.
    """
    target = str(getattr(candidate, "target_path", "") or "")
    if _is_self_build_target_allowed(target):
        return True
    try:
        mapping = map_backlog_candidate(
            candidate,
            workspace=workspace,
            allowed_targets=DEFAULT_CANDIDATE_TARGETS,
        )
    except Exception:  # noqa: BLE001 — a broken mapper must never break selection
        return False
    return bool(
        mapping.ok
        and mapping.candidate is not None
        and not _is_critical(mapping.candidate.target_path)
    )


def _default_grounded_selector(workspace: str | Path) -> Callable[[], Any]:
    """Build the DEFAULT grounded backlog selector for a workspace (TD-036
    follow-up).

    This is what makes the grounded path the default for the real callers
    (``:self-build-produce`` and the daemon) without them having to assemble a
    selector themselves. It reads ``TECH_DEBT.md``, ``docs/AGENT_ANATOMY.md``,
    the TD-038 slice 2 proposal doc, and the value-review ledger strictly
    read-only and returns a zero-arg
    callable yielding the top-ranked backlog candidate, or ``None`` when the
    closed backlog set is empty.

    It never calls an LLM, never touches the network/git, and is fully
    best-effort: any import/load failure yields a selector that returns
    ``None``, so the Manager refuses (``no_patch``) instead of silently falling
    back to the LLM manager.
    """
    def _select() -> Any:
        try:
            from core.backlog_selector import load_backlog, select_top

            reviews = None
            try:
                from core.value_review import ValueReviewLog

                reviews = ValueReviewLog.for_workspace(workspace).list()
            except Exception:  # noqa: BLE001 — reviews are an optional signal
                reviews = None
            candidates = load_backlog(workspace, value_reviews=reviews)
            # Provod #1 follow-up: candidates are ranked highest-first. Prefer the
            # top-ranked candidate the producer can actually act on — directly
            # low-risk OR mapper-resolvable to an allowed concrete file — so a
            # higher-ranked but non-actionable target (e.g. a missing
            # ``.github/workflows/ci.yml``) does not shadow an actionable one, yet
            # a mappable tech-debt item is NOT skipped for a lower-ranked audit
            # item. If nothing is actionable, fall back to the #1 (honest refusal).
            for candidate in candidates:
                if _grounded_candidate_actionable(candidate, workspace):
                    return candidate
            return select_top(candidates)
        except Exception:  # noqa: BLE001 — a broken backlog must never break producer
            return None

    return _select


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


def _normalize_builder_files(
    parsed: dict[str, Any], target: str
) -> tuple[list[dict[str, str]], str]:
    """Normalise a builder LLM reply into a canonical ``files`` list.

    Returns ``(files, primary_content)`` where ``primary_content`` is the content
    of the ``target`` file (used for the value gate, dedup digest, and legacy
    single-file back-compat). Two reply shapes are accepted:

    * legacy single-file ``{"content": "<full file>"}`` → one-element list
      ``[{"path": target, "content": content}]`` (byte-identical to the previous
      behaviour), and
    * multi-file split ``{"files": [{"path","content"}, ...]}`` → the listed
      files verbatim (paths normalised to forward slashes, deduped, target's own
      content surfaced as ``primary_content``).

    Malformed entries are dropped defensively; an empty/invalid result yields
    ``([], "")`` so the Critic vetoes rather than the producer raising.
    """
    raw_files = parsed.get("files")
    files: list[dict[str, str]] = []
    if isinstance(raw_files, (list, tuple)) and raw_files:
        seen: set[str] = set()
        for entry in raw_files:
            if not isinstance(entry, dict):
                continue
            path = entry.get("path")
            content = entry.get("content")
            if not isinstance(path, str) or not path.strip():
                continue
            if not isinstance(content, str):
                content = ""
            norm = path.replace("\\", "/").strip()
            if norm in seen:
                continue
            seen.add(norm)
            files.append({"path": norm, "content": content})
    else:
        content = parsed.get("content")
        if not isinstance(content, str):
            content = ""
        files.append({"path": target, "content": content})

    norm_target = target.replace("\\", "/").strip()
    primary = next(
        (f["content"] for f in files if f["path"] == norm_target),
        "",
    )
    return files, primary


def _builder_generate(
    llm: Any,
    target: str,
    current_content: str,
    diagnosis: str,
    *,
    split_mode: bool = False,
) -> RoleOutput:
    """Generate the FULL proposed file content (never a diff).

    When ``split_mode`` is True the Builder is asked to split the oversized
    target module into several smaller files: it must return the shrunk target
    plus one or more NEW sibling modules, each as complete file content. The
    single-file path (``split_mode=False``) is unchanged and byte-identical to
    the historical behaviour.
    """
    safe_content, inj = prepare_untrusted_text_for_llm(
        current_content or "",
        source_label=f"self_build:{target}",
    )
    if safe_content is None:
        return RoleOutput(
            "builder",
            "failed",
            "file content blocked by injection guard",
            {"injection": inj.to_log_payload(), "target": target},
        )
    if split_mode:
        system = (
            "You are the Builder. Split the oversized target Python module into "
            "several SMALLER, cohesive files to reduce its size while preserving "
            "behaviour and its public API exactly. Return the COMPLETE content of "
            "the shrunk target file AND one or more NEW sibling modules (in the "
            "same package directory) that it imports from. Do NOT modify any other "
            "existing file. Every file must be the entire file content, never a "
            "diff. Reply with strict JSON only: "
            '{"files": [{"path": "<repo-relative path>", "content": "<full file>"}], '
            '"test_paths": ["tests"], '
            '"test_pattern": "<optional pytest -k pattern or null>", '
            '"reason": "<one sentence>", "confidence": <0..1>}. '
            "Exactly one file path must equal the target; the rest must be new "
            "modules. Keep every file well under the size limit."
        )
    else:
        system = (
            "You are the Builder. Produce the COMPLETE new content of the target "
            "file — the entire file, not a diff or patch. Keep the change small and "
            "low-risk. Reply with strict JSON only: "
            '{"content": "<full file content>", "test_paths": ["tests"], '
            '"test_pattern": "<optional pytest -k pattern or null>", '
            '"reason": "<one sentence>", "confidence": <0..1>}.'
        )
    if _is_self_build_doc_target(target):
        system = f"{system}\n\n{_SELF_BUILD_DOC_GUIDANCE}"
    user = (
        f"Target file: {target}\n"
        f"Diagnosis: {diagnosis or '(none)'}\n\n"
        f"Current content:\n{safe_content or '(empty / new file)'}"
    )
    parsed = _llm_json(llm, system=system, user=user, max_tokens=_BUILDER_MAX_TOKENS)
    if not parsed:
        return RoleOutput(
            "builder", "failed", "builder returned no parseable JSON"
        )
    files, content = _normalize_builder_files(parsed, target)
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
    extra = len(files) - 1
    detail = f"generated {len(content)} chars (confidence {confidence:.2f})"
    if extra > 0:
        detail = (
            f"generated {len(content)} chars for target + {extra} new file(s) "
            f"(confidence {confidence:.2f})"
        )
    return RoleOutput(
        "builder",
        "built" if content else "failed",
        detail,
        {
            "content": content,
            "files": files,
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
    if _is_self_build_doc_target(target):
        missing_topics = _missing_self_build_doc_topics(content)
        if missing_topics:
            veto.append(
                "docs/self_build.md is not a human-gated self-build operator "
                "guide; missing topics: " + ", ".join(missing_topics)
            )
    if not build.get("test_paths"):
        veto.append("no targeted tests specified")
    if confidence < confidence_threshold:
        veto.append(
            f"confidence {confidence:.2f} below threshold {confidence_threshold:.2f}"
        )

    files = build.get("files") or [{"path": target, "content": content}]
    norm_target = target.replace("\\", "/").strip()
    # Multi-file (split) guard: validate every EXTRA file the same way the target
    # is validated above. The target itself is already checked, so it is skipped
    # here — keeping the single-file path byte-identical (no extra files).
    for entry in files:
        path = str(entry.get("path") or "").replace("\\", "/").strip()
        if not path or path == norm_target:
            continue
        body = entry.get("content") or ""
        if not isinstance(body, str) or not body.strip():
            veto.append(f"new file {path} has empty content")
        if _is_critical(path):
            veto.append(f"file {path} is a critical file")
        if body and _looks_like_diff(body):
            veto.append(f"file {path} looks like a diff, not full content")
        if len(body.encode("utf-8")) > _MAX_CONTENT_BYTES:
            veto.append(f"file {path} exceeds {_MAX_CONTENT_BYTES} bytes")
        if path.lower().endswith(".py") and body and not _looks_like_diff(body):
            try:
                ast.parse(body)
            except SyntaxError as exc:
                veto.append(f"new file {path} does not parse: {exc.msg}")

    ok, risk_reason, rejected = classify_patch_risk(
        [
            FileChange(path=str(f.get("path") or ""), content=f.get("content") or "")
            for f in files
        ]
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
    files = build.get("files") or [{"path": target, "content": content}]
    payload = build_self_apply_payload(
        files=[
            {"path": str(f.get("path") or ""), "content": f.get("content") or ""}
            for f in files
        ],
        reason=build.get("reason") or "",
        evidence=evidence,
        test_paths=build.get("test_paths") or ["tests"],
        test_pattern=build.get("test_pattern"),
        origin=PRODUCER_ORIGIN,
        rollback=DEFAULT_ROLLBACK,
    )
    digest = hashlib.sha1(content.encode("utf-8")).hexdigest()[:12]
    dedup_key = f"self_apply:{target}:{digest}"
    extra = len(files) - 1
    summary = f"self-apply proposal for {target}"
    if extra > 0:
        summary = f"self-apply split proposal for {target} (+{extra} new file(s))"
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
    grounded_selector: Callable[[], Any] | None = None,
    legacy_llm_manager: bool = False,
) -> ProducerReport:
    """Run the Manager/Researcher/Builder/Critic/Reporter pipeline to produce at
    most one validated low-risk self-apply proposal into the approval inbox.

    Returns a :class:`ProducerReport`. The function never applies the patch,
    never runs the lane, and creates at most one inbox item per call.

    If ``registry`` (a ``SubagentRegistry``) is provided, the run's role outcomes
    are recorded into it as an additive, best-effort side effect (TD-028). When
    ``registry is None`` — the default — behaviour is byte-identical to before,
    and a registry write failure can never change or break this function.

    Manager target selection (TD-036 follow-up): by default the Manager takes its
    target + diagnosis from a *grounded* backlog candidate (TECH_DEBT.md /
    docs/AGENT_ANATOMY.md / TD-038 slice 2 proposal doc), never inventing one
    via the LLM. Pass an explicit
    ``grounded_selector`` (a zero-arg callable returning a candidate or ``None``)
    to override the default workspace-backed selector. An empty backlog yields
    ``no_patch`` — there is no LLM fallback. The legacy LLM manager (which invents
    a diagnosis from the static allowlist) runs ONLY when explicitly requested via
    ``legacy_llm_manager=True`` (debug/tests).
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
    # TD-036 follow-up: the grounded backlog selector is now the DEFAULT source
    # of the Manager's target + diagnosis, so the real callers
    # (``:self-build-produce`` and the daemon) stop inventing a diagnosis via the
    # LLM. Resolution order:
    #   * an explicit ``grounded_selector`` (tests / advanced callers), else
    #   * the default workspace-backed grounded selector.
    # There is NO LLM fallback: an empty grounded backlog yields ``no_patch``
    # rather than a fabricated diagnosis. The legacy LLM manager path runs ONLY
    # when explicitly opted into via ``legacy_llm_manager=True`` (debug/tests).
    if legacy_llm_manager:
        manager = _manager_select(llm, targets)
    else:
        selector = grounded_selector or _default_grounded_selector(workspace)
        manager = _manager_from_grounded(selector, targets, workspace=workspace)
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
    split_mode = manager.data.get("mapping_rule") == "split_module"

    # ── Researcher ──────────────────────────────────────────────────────────
    researcher = _researcher_gather(reader, target, diagnosis)
    roles.append(researcher)
    current_content = researcher.data["current_content"]
    evidence = list(researcher.data["evidence"])

    # ── Builder ─────────────────────────────────────────────────────────────
    builder = _builder_generate(
        llm, target, current_content, diagnosis, split_mode=split_mode
    )
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

    # ── Value gate (TD-035): reject no-effect changes before publishing ─────
    # Runs after the Critic's *technical* pass and before the Reporter creates
    # an approval item. A hard veto (no code effect) blocks publication and
    # creates NO inbox item; soft flags are attached to the evidence so a human
    # reviewer sees them, but never block. Doc (*.md) targets are exempt from
    # hard veto. Deterministic — no LLM/network/git side effects.
    value = evaluate_proposal_value(
        target,
        current_content,
        builder.data.get("content") or "",
        builder.data.get("reason") or "",
    )
    if value.vetoed:
        return _record(ProducerReport(
            status="value_veto",
            reason="; ".join(value.veto_reasons) or "no-effect change",
            target_path=target,
            checked_gates=gates,
            role_outputs=roles,
            veto_reasons=list(value.veto_reasons),
            value_flags=list(value.flags),
            next_human_action="Value gate vetoed a no-effect change; no approval created.",
        ))
    if value.flags:
        evidence.extend(f"value-flag: {flag}" for flag in value.flags)

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
        value_flags=list(value.flags),
        next_human_action=(
            f"Review approval item {approval_id} and run "
            f":self-apply-run {approval_id} to apply it."
        ),
    ))
