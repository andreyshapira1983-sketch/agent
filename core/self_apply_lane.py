"""Trusted low-risk self-apply lane (TD-023).

This closes the first *safe* self-build loop:

    propose -> classify low-risk -> apply on a temp branch -> targeted tests
    -> full pytest -> rollback (red) OR local commit (green)

It is intentionally NOT general autonomous write access. The lane only touches a
narrow allowlist of source/test/docs files, works exclusively on a dedicated
temporary branch, never pushes, never merges to the base branch, and rolls the
working tree back automatically the moment any test stage fails.

Hard safety gates (checked before any file is touched, first trip wins):
  1. budget kill-switch active   -> status="budget_kill_switch"
  2. hour budget near-exhaustion -> status="budget_wait"
  3. pending approvals           -> status="approval_wait"
  4. patch not classified low-risk / denylisted -> status="rejected"
  5. workspace not clean         -> status="rejected"

The heavy dependencies (git, test runner) are injected so the whole lane is
unit-testable with fakes — no real provider/network/LLM call ever happens here.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Protocol

from core.budget_kill_switch import BudgetKillSwitch, default_path
from core.safe_vcs import SafeVCS, VcsError
from core.self_build_supervisor import (
    hour_budget_headroom,
    is_budget_near_exhaustion,
)

# Directories whose ``*.py`` files may be auto-applied in this lane.
_ALLOWED_CODE_DIRS = ("core", "cli", "tools", "tests")

# Explicit denylist — checked *before* the allowlist so a sensitive path can
# never slip through even if it also happens to look allowlisted (e.g. a
# ``.md`` file under ``.github``). Matching is by exact relative path or by a
# path prefix / filename rule.
_DENY_EXACT = frozenset(
    {
        "config/budget_limits.json",
        "config/model_registry.json",
        "config/model_catalog.json",
        "config/budget_limits.example.json",
        "config/model_registry.example.json",
    }
)
_DENY_PREFIXES = (".github/", ".git/", "config/", "secrets/", ".venv/")
_DENY_SUFFIXES = (".lock", ".pem", ".key")
_DENY_NAMES = frozenset(
    {
        ".env",
        "requirements.txt",
        "requirements-dev.txt",
        "poetry.lock",
        "package-lock.json",
        "pipfile.lock",
        "id_rsa",
        "credentials",
        "credentials.json",
    }
)
# Filenames that clearly denote secrets/keys/env regardless of directory.
_DENY_NAME_SUBSTR = ("secret", "credential", ".env")


@dataclass(frozen=True)
class FileChange:
    """One full-content file replacement (create or overwrite)."""

    path: str
    content: str


@dataclass(frozen=True)
class SelfApplyProposal:
    """A validated low-risk patch to route through the trusted apply lane."""

    files: tuple[FileChange, ...]
    reason: str = ""
    evidence: tuple[str, ...] = ()
    test_paths: tuple[str, ...] = ("tests",)
    test_pattern: str | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "files", tuple(self.files))
        object.__setattr__(self, "evidence", tuple(self.evidence))
        object.__setattr__(self, "test_paths", tuple(self.test_paths))


class TestRunner(Protocol):
    """Subset of ``tools.run_tests.RunTestsTool`` the lane depends on."""

    def run(
        self,
        paths: list[str] | None = ...,
        pattern: str | None = ...,
    ) -> dict[str, Any]: ...


@dataclass
class SelfApplyReport:
    status: str
    reason: str = ""
    branch: str | None = None
    files_changed: list[str] = field(default_factory=list)
    tests_run: list[str] = field(default_factory=list)
    rollback_status: str = "none"  # none | restored | failed
    commit_hash: str | None = None
    rejected_files: list[str] = field(default_factory=list)
    risks: list[str] = field(default_factory=list)
    next_human_action: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status,
            "reason": self.reason,
            "branch": self.branch,
            "files_changed": list(self.files_changed),
            "tests_run": list(self.tests_run),
            "rollback_status": self.rollback_status,
            "commit_hash": self.commit_hash,
            "rejected_files": list(self.rejected_files),
            "risks": list(self.risks),
            "next_human_action": self.next_human_action,
        }


# ── risk classification ─────────────────────────────────────────────────────


def _normalize_rel(path: str) -> str | None:
    """Return a clean forward-slash relative path, or None if unsafe/absolute."""
    if not isinstance(path, str) or not path.strip():
        return None
    p = path.replace("\\", "/").strip()
    if p.startswith("/") or (len(p) > 1 and p[1] == ":"):  # absolute / drive
        return None
    parts = [seg for seg in p.split("/") if seg not in ("", ".")]
    if any(seg == ".." for seg in parts):
        return None
    return "/".join(parts)


def _is_denied(rel: str) -> bool:
    lower = rel.lower()
    name = lower.rsplit("/", 1)[-1]
    if lower in {d.lower() for d in _DENY_EXACT}:
        return True
    if any(lower.startswith(pfx) for pfx in _DENY_PREFIXES):
        return True
    if any(lower.endswith(sfx) for sfx in _DENY_SUFFIXES):
        return True
    if name in _DENY_NAMES:
        return True
    if any(sub in name for sub in _DENY_NAME_SUBSTR):
        return True
    return False


def _is_allowed(rel: str) -> bool:
    lower = rel.lower()
    top = lower.split("/", 1)[0]
    if top in _ALLOWED_CODE_DIRS and lower.endswith(".py"):
        return True
    if top == "docs":
        return True
    if lower.endswith(".md"):
        return True
    return False


def classify_patch_risk(
    files: tuple[FileChange, ...] | list[FileChange],
) -> tuple[bool, str, list[str]]:
    """Pure low-risk gate: every file must be allowlisted and not denylisted.

    Returns ``(ok, reason, rejected_files)``. ``ok`` is True only when the patch
    touches at least one file and every file passes both checks.
    """
    files = list(files)
    if not files:
        return False, "empty patch: no files to apply", []
    rejected: list[str] = []
    for change in files:
        rel = _normalize_rel(change.path)
        if rel is None:
            rejected.append(change.path)
            continue
        if _is_denied(rel) or not _is_allowed(rel):
            rejected.append(change.path)
    if rejected:
        return (
            False,
            "patch is not low-risk: files outside the allowlist or on the "
            "denylist",
            rejected,
        )
    return True, "all files are low-risk (allowlisted, not denylisted)", []


# ── helpers ─────────────────────────────────────────────────────────────────


def _tests_ok(result: Any) -> bool:
    if not isinstance(result, dict):
        return False
    return (
        result.get("timed_out") is False
        and result.get("exit_code") == 0
        and int(result.get("failed") or 0) == 0
        and int(result.get("errors") or 0) == 0
    )


def _write_file(workspace: Path, rel: str, content: str) -> None:
    target = (workspace / rel).resolve()
    root = workspace.resolve()
    if root not in target.parents and target != root:
        raise VcsError(f"refusing to write outside workspace: {rel}")
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(content, encoding="utf-8")


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")


# ── orchestration ───────────────────────────────────────────────────────────


def run_self_apply_lane(
    proposal: SelfApplyProposal,
    *,
    workspace: Path,
    vcs: SafeVCS,
    test_runner: TestRunner,
    budget_snapshot: dict | None = None,
    approvals_pending: int = 0,
    kill_switch: BudgetKillSwitch | None = None,
    branch_prefix: str = "self-apply",
    now_iso: str | None = None,
) -> SelfApplyReport:
    """Run the trusted low-risk self-apply loop end to end.

    Returns a structured :class:`SelfApplyReport`. Never pushes, never merges,
    never edits the base branch directly, and rolls back automatically on any
    test failure or unexpected error.
    """
    # 1. budget kill-switch ---------------------------------------------------
    switch = kill_switch or BudgetKillSwitch(path=default_path(workspace))
    ks_state = switch.status(budget_snapshot, now_iso=now_iso)  # read-only
    if ks_state.active:
        return SelfApplyReport(
            status="budget_kill_switch",
            reason=(
                f"budget kill-switch active: {ks_state.window} "
                f"{ks_state.counter} {ks_state.used}/{ks_state.limit}"
            ),
            next_human_action=(
                "Clear the kill-switch (:budget-kill-switch --clear) once the "
                "day budget window has recovered."
            ),
        )

    # 2. budget near-exhaustion ----------------------------------------------
    near, budget_reasons = is_budget_near_exhaustion(
        hour_budget_headroom(budget_snapshot or {})
    )
    if near:
        return SelfApplyReport(
            status="budget_wait",
            reason="; ".join(budget_reasons),
            next_human_action=(
                "Wait for the hour budget window to refill before retrying the "
                "self-apply lane."
            ),
        )

    # 3. pending approvals ----------------------------------------------------
    pending = int(approvals_pending or 0)
    if pending > 0:
        return SelfApplyReport(
            status="approval_wait",
            reason=f"{pending} approval item(s) pending",
            next_human_action=(
                "Clear the approval queue (:approval-list / :approval-triage) "
                "before running the self-apply lane."
            ),
        )

    # 4. low-risk classification ---------------------------------------------
    ok, risk_reason, rejected = classify_patch_risk(proposal.files)
    if not ok:
        return SelfApplyReport(
            status="rejected",
            reason=risk_reason,
            rejected_files=rejected,
            risks=[risk_reason],
            next_human_action=(
                "Narrow the patch to allowlisted source/test/docs files, or "
                "route sensitive changes through explicit human approval."
            ),
        )

    files_changed = [_normalize_rel(c.path) or c.path for c in proposal.files]

    # 5. require a clean working tree ----------------------------------------
    try:
        if not vcs.is_clean():
            return SelfApplyReport(
                status="rejected",
                reason="workspace has uncommitted changes; refusing to apply",
                next_human_action=(
                    "Commit or stash existing changes so the lane starts from a "
                    "clean tree."
                ),
            )
        original_branch = vcs.current_branch()
    except VcsError as exc:
        return SelfApplyReport(
            status="error",
            reason=f"vcs precheck failed: {exc}",
            next_human_action="Inspect the repository state manually.",
        )

    branch = f"{branch_prefix}/{_timestamp()}"
    next_human = (
        f"Review branch {branch}, then merge/push manually if the change is "
        "acceptable."
    )

    def _rollback() -> str:
        try:
            vcs.reset_hard()
            vcs.clean_untracked()
            vcs.checkout(original_branch)
            vcs.delete_branch(branch)
            return "restored"
        except VcsError:
            return "failed"

    # 6. create temp branch + apply ------------------------------------------
    try:
        vcs.create_temp_branch(branch)
    except VcsError as exc:
        return SelfApplyReport(
            status="error",
            reason=f"could not create temp branch: {exc}",
            next_human_action="Inspect the repository state manually.",
        )

    try:
        for change in proposal.files:
            rel = _normalize_rel(change.path) or change.path
            _write_file(workspace, rel, change.content)
    except (VcsError, OSError) as exc:
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason=f"apply failed: {exc}",
            branch=branch,
            files_changed=files_changed,
            rollback_status=rollback_status,
            next_human_action="Inspect the proposal; the tree was restored.",
        )

    # 7. targeted tests -------------------------------------------------------
    tests_run: list[str] = []
    targeted = test_runner.run(
        paths=list(proposal.test_paths),
        pattern=proposal.test_pattern,
    )
    tests_run.append("targeted")
    if not _tests_ok(targeted):
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason="targeted tests failed",
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            risks=["targeted tests red; change discarded"],
            next_human_action="Review the failing targeted tests; tree restored.",
        )

    # 8. full pytest ----------------------------------------------------------
    full = test_runner.run(paths=None, pattern=None)
    tests_run.append("full")
    if not _tests_ok(full):
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason="full pytest failed",
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            risks=["full suite red; change discarded"],
            next_human_action="Review the failing full suite; tree restored.",
        )

    # 9. local commit (no push, no merge) ------------------------------------
    message = _commit_message(proposal, branch)
    try:
        vcs.stage_all()
        commit_hash = vcs.commit(message)
        vcs.checkout(original_branch)  # leave the repo on its original branch
    except VcsError as exc:
        rollback_status = _rollback()
        return SelfApplyReport(
            status="rolled_back",
            reason=f"local commit failed: {exc}",
            branch=branch,
            files_changed=files_changed,
            tests_run=tests_run,
            rollback_status=rollback_status,
            next_human_action="Inspect the repository state manually.",
        )

    return SelfApplyReport(
        status="committed_local",
        reason="targeted + full tests passed; committed locally on temp branch",
        branch=branch,
        files_changed=files_changed,
        tests_run=tests_run,
        rollback_status="none",
        commit_hash=commit_hash,
        risks=[
            "change is committed locally only — not pushed, not merged; a human "
            "must review before it reaches the base branch"
        ],
        next_human_action=next_human,
    )


def _commit_message(proposal: SelfApplyProposal, branch: str) -> str:
    head = proposal.reason.strip() or "self-apply: low-risk automated patch"
    body_files = ", ".join(
        _normalize_rel(c.path) or c.path for c in proposal.files
    )
    return f"{head}\n\nself-apply lane ({branch})\nfiles: {body_files}"
