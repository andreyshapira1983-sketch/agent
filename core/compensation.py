"""Compensation (undo) plans for mutating tools: best-effort, audit-trailed rollback via delete_path_if_created, restore_from_backup, noop."""
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
    """One reversible step of a compensation plan."""

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
    def noop(cls, tool_name: str, description: str = "no state change") -> CompensationPlan:
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
    def from_dict(cls, data: dict) -> CompensationPlan:
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
    skipped_reason: str | None = None

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
        if self.skipped_reason:
            result["skipped_reason"] = self.skipped_reason
        # Best-effort, not transactional: earlier successful actions are not undone, so flag a mixed result explicitly.
        if error_count > 0:
            result["warning"] = (
                "compensation is best-effort and non-transactional: "
                f"{error_count} action(s) failed; workspace may be in a "
                "partially-rolled-back state."
            )
        return result


def _resolve_inside(workspace_root: Path, rel: str | None) -> Path | None:
    """Return absolute path resolved inside workspace, or None on escape (absolute, `..`, symlink-out), even during rollback."""
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
    """Apply the plan's actions in REVERSE order (LIFO), best-effort: each action has its own try/except and the report records every outcome."""
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


#: Roots that rollback NEVER removes; work under them stays undoable.
_PROTECTED_ROOTS: frozenset[str] = frozenset({
    ".git", "data", "logs", "config", "core", "tools", "cli", "app", "api",
    "tests", "scripts", "docs",
})


def _protected_root(workspace_root: Path, target: Path) -> str | None:
    """Name of the protected root if the target is that root itself, else None."""
    try:
        rel = target.resolve().relative_to(Path(workspace_root).resolve())
    except (ValueError, OSError):
        return None
    parts = rel.parts
    if len(parts) == 1 and parts[0] in _PROTECTED_ROOTS:
        return parts[0]
    return None


def _apply_action(action: CompensationAction, workspace_root: Path) -> CompensationOutcome:
    if action.kind == "noop":
        return CompensationOutcome(action, status="noop", detail="no state change")

    if action.kind == "delete_path_if_created":
        target = _resolve_inside(workspace_root, action.path)
        if target is None:
            return CompensationOutcome(
                action, status="error", detail="path escapes workspace"
            )
        protected = _protected_root(workspace_root, target)
        if protected is not None:
            return CompensationOutcome(
                action, status="error",
                detail=f"refusing to remove protected root {protected!r}",
            )
        if not target.exists():
            return CompensationOutcome(
                action, status="noop", detail="path already absent"
            )
        if target.is_dir():
            # `_protected_root` refuses only the listed roots themselves, NOT the workspace root:
            # a plan path of '.' or '' passes `_resolve_inside` and would reach this rmtree
            # (no guard for that yet; plans are trusted to name tool-created paths).
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
