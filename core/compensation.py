"""Compensation System (§5 Undo) — first introduced for MVP-11 shell_exec.

The contract is simple and intentionally narrow:

  1. BEFORE a tool mutates state, it must build a CompensationPlan
     describing what to do to undo the change.
  2. AFTER the mutation succeeds, the tool surfaces the plan as part of
     its structured output.
  3. The AgentLoop captures the plan (audit log + in-memory registry).
  4. The user (or a future automatic re-planner) can call `rollback`
     to apply the plan in reverse.

Compensation is **not** a transaction system. It is a best-effort, audit-
trailed undo. It exists because some tools (shell_exec, file_write) are
irreversible by nature, and the agent must be able to undo what it did
when a subsequent step fails or a human asks "actually, undo that".

Supported action kinds (deliberately small):

  - `delete_path_if_created` — remove a file/directory that was NOT
    present before the tool ran (i.e. the tool itself created it)
  - `restore_from_backup`    — copy a backup file back over a target,
    then remove the backup (used by file_write overwrites)
  - `noop`                   — placeholder for read-only operations
                               that still want an audit trail entry

Every Action carries enough context to rollback IDEMPOTENTLY: a second
apply() is a no-op, not a failure. Paths are sandboxed to the workspace
root passed at apply-time — a compensation plan from one workspace
cannot reach into another.
"""
from __future__ import annotations

import shutil
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

from core.ids import new_id


ActionKind = Literal["delete_path_if_created", "restore_from_backup", "noop"]


@dataclass(frozen=True)
class CompensationAction:
    """One reversible step of a compensation plan.

    Two action kinds are concrete today, plus a noop placeholder:

      delete_path_if_created
        path: str — workspace-relative path to remove
        Used after a tool *created* a new path. Idempotent: if the
        path was already deleted (by a human, or by an earlier apply),
        the action succeeds silently.

      restore_from_backup
        path: str        — workspace-relative target file
        backup_path: str — workspace-relative backup file holding the
                           pre-change content
        Used after a tool *overwrote* an existing file. The action
        copies backup_path back over path, then deletes the backup.
        Idempotent: if the backup is already gone, the action is a
        no-op.
    """

    kind: ActionKind
    description: str = ""
    path: str | None = None
    backup_path: str | None = None


@dataclass
class CompensationPlan:
    """A typed undo plan attached to a single tool invocation."""

    id: str = field(default_factory=lambda: new_id("comp"))
    tool_name: str = ""
    description: str = ""
    actions: list[CompensationAction] = field(default_factory=list)
    created_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    @classmethod
    def noop(cls, tool_name: str, description: str = "no state change") -> "CompensationPlan":
        return cls(
            tool_name=tool_name,
            description=description,
            actions=[CompensationAction(kind="noop", description=description)],
        )

    def to_dict(self) -> dict:
        """JSON-safe representation — what lives in tool output + audit log."""
        return {
            "id": self.id,
            "tool_name": self.tool_name,
            "description": self.description,
            "created_at": self.created_at.isoformat(),
            "actions": [
                {
                    "kind": a.kind,
                    "description": a.description,
                    "path": a.path,
                    "backup_path": a.backup_path,
                }
                for a in self.actions
            ],
        }

    @classmethod
    def from_dict(cls, data: dict) -> "CompensationPlan":
        """Reverse of `to_dict` for replay / cross-process use."""
        created = data.get("created_at")
        if isinstance(created, str):
            created_dt = datetime.fromisoformat(created)
        else:
            created_dt = datetime.now(timezone.utc)
        actions = [
            CompensationAction(
                kind=a["kind"],
                description=a.get("description", ""),
                path=a.get("path"),
                backup_path=a.get("backup_path"),
            )
            for a in data.get("actions", [])
        ]
        return cls(
            id=data.get("id") or new_id("comp"),
            tool_name=data.get("tool_name", ""),
            description=data.get("description", ""),
            actions=actions,
            created_at=created_dt,
        )


@dataclass
class CompensationOutcome:
    """Per-action result of a rollback attempt."""

    action: CompensationAction
    status: Literal["ok", "noop", "error"]
    detail: str = ""


@dataclass
class CompensationReport:
    """What happened during `apply_compensation_plan`."""

    plan_id: str
    workspace_root: str
    outcomes: list[CompensationOutcome] = field(default_factory=list)
    applied_at: datetime = field(default_factory=lambda: datetime.now(timezone.utc))

    def summary(self) -> dict:
        ok_count = sum(1 for o in self.outcomes if o.status == "ok")
        noop_count = sum(1 for o in self.outcomes if o.status == "noop")
        error_count = sum(1 for o in self.outcomes if o.status == "error")
        result: dict = {
            "plan_id": self.plan_id,
            "workspace_root": self.workspace_root,
            "applied_at": self.applied_at.isoformat(),
            "action_count": len(self.outcomes),
            "ok": ok_count,
            "noop": noop_count,
            "error": error_count,
            "outcomes": [
                {
                    "kind": o.action.kind,
                    "path": o.action.path,
                    "backup_path": o.action.backup_path,
                    "status": o.status,
                    "detail": o.detail,
                }
                for o in self.outcomes
            ],
        }
        # Compensation is best-effort, NOT transactional. A partial failure
        # leaves the workspace in an intermediate state — earlier actions
        # that succeeded are NOT automatically undone. Flag this explicitly
        # so callers never silently treat a mixed result as a clean rollback.
        if error_count > 0:
            result["warning"] = (
                "compensation is best-effort and non-transactional: "
                f"{error_count} action(s) failed; workspace may be in a "
                "partially-rolled-back state."
            )
        return result


def _resolve_inside(workspace_root: Path, rel: str | None) -> Path | None:
    """Return absolute path resolved inside workspace, or None on escape.

    Sandbox floor: any path that escapes the workspace (absolute on a
    different drive, `..` traversal, symlink-out) is rejected — even
    during ROLLBACK. A compensation plan can never delete arbitrary
    files even if its `path` was maliciously crafted.
    """
    if rel is None:
        return None
    workspace_root = Path(workspace_root).resolve()
    candidate = (workspace_root / rel).resolve()
    try:
        candidate.relative_to(workspace_root)
    except ValueError:
        return None
    return candidate


def apply_compensation_plan(
    plan: CompensationPlan,
    workspace_root: Path,
) -> CompensationReport:
    """Apply the plan's actions in REVERSE order (LIFO). Best-effort.

    Each action is wrapped in its own try/except: one failed action
    never aborts the rest of the plan. The report records the precise
    outcome of every action — including 'noop' for idempotent cases
    where the target was already in the desired state.
    """
    workspace_root = Path(workspace_root).resolve()
    report = CompensationReport(
        plan_id=plan.id,
        workspace_root=str(workspace_root),
    )

    for action in reversed(plan.actions):
        try:
            outcome = _apply_action(action, workspace_root)
        except Exception as exc:  # noqa: BLE001 — last line of defence
            outcome = CompensationOutcome(
                action=action,
                status="error",
                detail=f"{type(exc).__name__}: {exc}",
            )
        report.outcomes.append(outcome)
    return report


def _apply_action(action: CompensationAction, workspace_root: Path) -> CompensationOutcome:
    if action.kind == "noop":
        return CompensationOutcome(action, status="noop", detail="no state change")

    if action.kind == "delete_path_if_created":
        target = _resolve_inside(workspace_root, action.path)
        if target is None:
            return CompensationOutcome(
                action, status="error", detail="path escapes workspace"
            )
        if not target.exists():
            return CompensationOutcome(
                action, status="noop", detail="path already absent"
            )
        if target.is_dir():
            # rmtree is fine — we own the path (tool created it).
            shutil.rmtree(target)
        else:
            target.unlink()
        return CompensationOutcome(
            action, status="ok", detail=f"removed {action.path}"
        )

    if action.kind == "restore_from_backup":
        target = _resolve_inside(workspace_root, action.path)
        backup = _resolve_inside(workspace_root, action.backup_path)
        if target is None or backup is None:
            return CompensationOutcome(
                action, status="error", detail="path escapes workspace"
            )
        if not backup.exists():
            return CompensationOutcome(
                action, status="noop", detail="backup already gone"
            )
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(backup, target)
        backup.unlink()
        return CompensationOutcome(
            action, status="ok", detail=f"restored {action.path} from {action.backup_path}"
        )

    return CompensationOutcome(
        action, status="error", detail=f"unknown action kind: {action.kind}"
    )
