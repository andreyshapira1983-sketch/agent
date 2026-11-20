"""Compensation (undo) plans for mutating tools: best-effort, audit-trailed rollback via delete_path_if_created, restore_from_backup, noop."""
from __future__ import annotations

import os
import shutil
import stat
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


#: Roots that rollback NEVER removes; work under them stays undoable (except under .git, see _touches_git).
_PROTECTED_ROOTS: frozenset[str] = frozenset({
    ".git", "data", "logs", "config", "core", "tools", "cli", "app", "api",
    "tests", "scripts", "docs",
})
_PROTECTED_FOLDED: frozenset[str] = frozenset(n.casefold() for n in _PROTECTED_ROOTS)
_WINDOWS = os.name == "nt"
_ABSENT = (FileNotFoundError, NotADirectoryError)


def _norm_part(part: str) -> str:
    """A path part compared the way the file system may read it: case-folded, and on Windows without a stream suffix or trailing dots and spaces."""
    if _WINDOWS:
        part = part.split(":", 1)[0].rstrip(" .") or part
    return part.casefold()


def _as_addressed(workspace_root: Path, rel: str) -> Path:
    """The plan path as the OS will address it (Windows collapses 'x\\..' and trailing dots before acting)."""
    joined = workspace_root / rel
    return Path(os.path.abspath(joined)) if _WINDOWS else joined


def _entry(workspace_root: Path, rel: str) -> Path:
    """The directory entry a plan path names: parent resolved, last component NOT followed."""
    addressed = _as_addressed(workspace_root, rel)
    return Path(os.path.realpath(addressed.parent)) / addressed.name


def _is_link(path: Path) -> bool:
    """True for a symlink or a directory junction, judged from lstat alone (never follows it); real I/O errors propagate."""
    try:
        st = os.lstat(path)
    except _ABSENT:
        return False
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
                and getattr(st, "st_reparse_tag", 0) == getattr(stat, "IO_REPARSE_TAG_MOUNT_POINT", -1))


def _same_entry(a: Path, b: Path) -> bool:
    """Same directory entry on disk (catches case, 8.3 and other spellings the name check misses)."""
    try:
        return os.path.samestat(os.lstat(a), os.lstat(b))
    except _ABSENT:
        return False


def _overlaps(target: Path, home: Path, workspace_root: Path, *, inside_too: bool) -> bool:
    """True when removing `target` removes `home` (target is home or above it), or with inside_too when target lies inside home."""
    for anc in (home, *home.parents):
        if anc in (workspace_root, anc.parent):
            break
        if anc == target or _same_entry(anc, target):
            return True
    if inside_too:
        for anc in (target, *target.parents):
            if anc in (workspace_root, anc.parent):
                break
            if anc == home or _same_entry(anc, home):
                return True
    return False


def _link_chain(path: Path, limit: int = 40) -> list[Path]:
    """Every link entry met on the way from `path` to its final target, `path` included when it is a link."""
    hops: list[Path] = []
    current = path
    for _ in range(limit):
        if not _is_link(current):
            break
        hops.append(current)
        nxt = os.readlink(current).removeprefix("\\\\?\\")
        nxt_path = Path(nxt) if os.path.isabs(nxt) else current.parent / nxt
        current = Path(os.path.realpath(nxt_path.parent)) / nxt_path.name
    return hops


def _git_home(workspace_root: Path) -> Path | None:
    """Where the workspace's git data really lives when .git is a link or a 'gitdir:' file, else None."""
    git = workspace_root / ".git"
    if _is_link(git):
        return git.resolve()
    try:
        if git.is_file():
            first = git.read_text(encoding="utf-8", errors="replace").strip().splitlines()[:1]
            if first and first[0].startswith("gitdir:"):
                pointed = first[0][len("gitdir:"):].strip()
                return (Path(pointed) if os.path.isabs(pointed) else workspace_root / pointed).resolve()
    except _ABSENT:
        return None
    return None


def _touches_git(workspace_root: Path, rel: str | None, located: Path | None) -> bool:
    """True when the plan path or its real location is .git, inside it, or holds the real git data; no tool writes there."""
    if rel and any(_norm_part(p) == ".git" for p in Path(rel).parts):
        return True
    if located is None:
        return False
    try:
        if any(_norm_part(p) == ".git" for p in located.relative_to(workspace_root).parts):
            return True
    except ValueError:
        return False
    if _overlaps(located, workspace_root / ".git", workspace_root, inside_too=True):
        return True
    home = _git_home(workspace_root)
    return home is not None and _overlaps(located, home, workspace_root, inside_too=True)


def _protected_name(workspace_root: Path, entry: Path) -> str | None:
    """The protected root this top-level entry is, by folded name or by identity, else None."""
    if _norm_part(entry.name) in _PROTECTED_FOLDED:
        return entry.name
    for name in _PROTECTED_ROOTS:
        if _same_entry(entry, workspace_root / name):
            return name
    return None


def _protected_root(workspace_root: Path, target: Path) -> str | None:
    """The protected root that removing the resolved `target` would destroy: the root itself, or its real home behind a link."""
    ws = Path(workspace_root).resolve()
    try:
        target = target.resolve()
        rel = target.relative_to(ws)
    except (ValueError, OSError):
        return None
    if len(rel.parts) == 1:
        hit = _protected_name(ws, target)
        if hit is not None:
            return hit
    for name in sorted(_PROTECTED_ROOTS):
        root = ws / name
        if not os.path.lexists(root):
            continue
        try:
            real = root.resolve()
        except (OSError, RuntimeError):  # a looping or unreadable root cannot be verified: refuse rather than guess
            return name
        if _overlaps(target, real, ws, inside_too=False):
            return name
    return None


def _guarded_link(workspace_root: Path, own: Path) -> str | None:
    """Name of the protected root whose link chain passes through `own` (cutting it would leave the root dangling), else None."""
    for name in sorted(_PROTECTED_ROOTS):
        try:
            chain = _link_chain(workspace_root / name)
        except OSError:
            continue
        if any(hop == own or _same_entry(hop, own) for hop in chain):
            return name
    return None


def _control_file(workspace_root: Path, path: Path) -> str | None:
    from core.control_files import control_file_hit

    return control_file_hit(workspace_root, path)


def _error(action: CompensationAction, detail: str) -> CompensationOutcome:
    return CompensationOutcome(action, status="error", detail=detail)


def _remove_link(action: CompensationAction, workspace_root: Path, own: Path) -> CompensationOutcome:
    """Remove the link entry itself, never its target, only when it lives inside the workspace and guards nothing."""
    try:
        rel_own = own.relative_to(workspace_root)
    except ValueError:
        return _error(action, "link lives outside the workspace")
    if not rel_own.parts:
        return _error(action, "refusing to remove the workspace root")
    if _touches_git(workspace_root, action.path, own):
        return _error(action, "refusing to remove anything under .git")
    if own.parent == workspace_root:
        hit = _protected_name(workspace_root, own)
        if hit is not None:
            return _error(action, f"refusing to remove protected root {hit!r} (a link)")
    hit = _guarded_link(workspace_root, own)
    if hit is not None:
        return _error(action, f"refusing to cut the link chain of protected root {hit!r}")
    if _control_file(workspace_root, own) is not None:
        return _error(action, "refusing to remove an operator-only control file")
    try:
        dir_link = bool(getattr(os.lstat(own), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_DIRECTORY", 0))
        if dir_link:  # a Windows directory symlink or junction is removed with rmdir, which leaves the target alone
            os.rmdir(own)
        else:
            os.unlink(own)
    except OSError as exc:
        return _error(action, f"could not remove link: {type(exc).__name__}: {exc}")
    return CompensationOutcome(action, status="ok", detail=f"removed link {action.path}")


def _holds_git(directory: Path) -> bool:
    """True when a .git entry sits anywhere inside `directory` (links are not followed)."""
    for _root, dirs, files in os.walk(directory, followlinks=False):
        if any(_norm_part(n) == ".git" for n in (*dirs, *files)):
            return True
    return False


def _delete_created(action: CompensationAction, workspace_root: Path) -> CompensationOutcome:
    if not action.path:
        return _error(action, "refusing to remove the workspace root")
    own = _entry(workspace_root, action.path)
    if own == workspace_root:  # '.', './', 'x/..' on Windows: the plan names the workspace itself
        return _error(action, "refusing to remove the workspace root")
    if _is_link(own):
        return _remove_link(action, workspace_root, own)
    target = _resolve_inside(workspace_root, action.path)
    if target is None:
        return _error(action, "path escapes workspace")
    if target == workspace_root:
        return _error(action, "refusing to remove the workspace root")
    if _touches_git(workspace_root, action.path, target):
        return _error(action, "refusing to remove anything under .git")
    protected = _protected_root(workspace_root, target)
    if protected is not None:
        return _error(action, f"refusing to remove protected root {protected!r}")
    if _control_file(workspace_root, target) is not None:
        return _error(action, "refusing to remove an operator-only control file")
    if not target.exists():
        return CompensationOutcome(action, status="noop", detail="path already absent")
    if target.is_dir():
        if _holds_git(target):
            return _error(action, "refusing to remove a directory that holds a git repository")
        shutil.rmtree(target)
    else:
        target.unlink()
    return CompensationOutcome(action, status="ok", detail=f"removed {action.path}")


def _restore_paths(action: CompensationAction, workspace_root: Path) -> tuple[Path, Path] | str:
    """(target, backup) when both may be touched, else the reason to refuse."""
    if not action.path or not action.backup_path:
        return "restore needs both a target and a backup path"
    for role, rel in (("target", action.path), ("backup", action.backup_path)):
        if _is_link(_entry(workspace_root, rel)):
            return f"{role} path is a link; restore never follows one"
    target = _resolve_inside(workspace_root, action.path)
    backup = _resolve_inside(workspace_root, action.backup_path)
    if target is None or backup is None:
        return "path escapes workspace"
    if target == workspace_root:
        return "refusing to restore over the workspace root"
    if _touches_git(workspace_root, action.path, target) or _touches_git(workspace_root, action.backup_path, backup):
        return "refusing to touch anything under .git"
    for role, path in (("target", target), ("backup", backup)):
        protected = _protected_root(workspace_root, path)
        if protected is not None:
            return f"refusing to touch protected root {protected!r} ({role})"
        if _control_file(workspace_root, path) is not None:
            return f"refusing to touch an operator-only control file ({role})"
    if target.is_dir():
        return "restore target is a directory"
    return target, backup


def _restore_backup(action: CompensationAction, workspace_root: Path) -> CompensationOutcome:
    checked = _restore_paths(action, workspace_root)
    if isinstance(checked, str):
        return _error(action, checked)
    target, backup = checked
    if not backup.exists():
        return CompensationOutcome(action, status="noop", detail="backup already gone; the target was not restored by this run")
    backup_stat = os.lstat(backup)
    if not stat.S_ISREG(backup_stat.st_mode):
        return _error(action, "backup is not a regular file")
    if backup.parent != target.parent or not backup.name.startswith(target.name + ".bak."):
        return _error(action, "backup does not belong to the target (expected '<target>.bak.<stamp>' beside it)")
    if backup_stat.st_nlink > 1 or (os.path.lexists(target) and os.lstat(target).st_nlink > 1):
        return _error(action, "refusing to restore through a hard link (the same file lives elsewhere too)")
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(backup, target)
    try:
        backup.unlink()
    except OSError as exc:
        return CompensationOutcome(action, status="ok", detail=f"restored {action.path}; backup left behind ({type(exc).__name__})")
    return CompensationOutcome(action, status="ok", detail=f"restored {action.path} from {action.backup_path}")


def _apply_action(action: CompensationAction, workspace_root: Path) -> CompensationOutcome:
    workspace_root = Path(workspace_root).resolve()  # every guard below compares canonical paths
    if action.kind == "noop":
        return CompensationOutcome(action, status="noop", detail="no state change")
    if action.kind == "delete_path_if_created":
        return _delete_created(action, workspace_root)
    if action.kind == "restore_from_backup":
        return _restore_backup(action, workspace_root)
    return _error(action, f"unknown action kind: {action.kind}")
