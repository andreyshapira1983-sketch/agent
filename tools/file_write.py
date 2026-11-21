"""file_write tool: write a UTF-8 file inside the workspace; overwrites keep a backup."""
from __future__ import annotations

import calendar
import os
import shutil
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from core.compensation import CompensationAction, CompensationPlan, touches_git
from core.control_files import control_file_hit, refuse_message
from core.secret_scanner import contains_secret
from tools.base import Risk, Tool, require_ascii_identifier

MAX_BYTES = 1 * 1024 * 1024  # 1 MiB


def _reserve_backup(target: Path) -> Path:
    """Claim a fresh `<file>.bak.<UTC stamp>` name; a taken stamp moves on, never overwriting."""
    seconds = calendar.timegm(time.gmtime())
    for step in range(3600):
        stamp = datetime.fromtimestamp(seconds + step, timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        candidate = target.with_suffix(target.suffix + f".bak.{stamp}")
        if os.path.lexists(candidate):  # also a dangling link: O_EXCL would follow it on Windows
            continue
        try:
            os.close(os.open(candidate, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
        except FileExistsError:
            continue
        return candidate
    raise FileExistsError(f"no free backup name for {target.name} within an hour of stamps")


class FileWriteTool(Tool):
    name = "file_write"
    description = (
        "Write a UTF-8 text file inside the workspace. Creating a new "
        "file is reversible; overwriting an existing file is escalated "
        "to human approval and a timestamped backup is kept. Refuses "
        "to write paths outside the workspace or content that contains "
        "credentials."
    )
    # Static fallback; PolicyGate reads the per-call risk from risk_for().
    risk: Risk = "irreversible"

    def __init__(self, workspace_root: Path, max_bytes: int = MAX_BYTES):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------
    # path resolution + sandbox
    # ------------------------------------------------------------------
    def _resolve(self, raw_path: str) -> Path:
        # Paths are ASCII identifiers; non-ASCII text is allowed only in content.
        require_ascii_identifier(raw_path, role="file_write path")
        candidate = (self.workspace_root / raw_path).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(
                f"Path escapes workspace: {candidate}"
            ) from exc
        # git runs programs named inside .git (hooks, filters, config).
        if touches_git(self.workspace_root, candidate):
            raise PermissionError(
                f"refusing to write under .git: {raw_path!r} — the repository's "
                "own folder is not the agent's to edit"
            )
        # Brake files are the operator's only, even with approval (core/control_files.py).
        guarded = control_file_hit(self.workspace_root, candidate)
        if guarded:
            raise PermissionError(refuse_message(guarded))
        return candidate

    # ------------------------------------------------------------------
    # Tool.risk_for: dynamic risk based on whether the path exists
    # ------------------------------------------------------------------
    def risk_for(self, arguments: dict[str, Any]) -> Risk:
        raw_path = arguments.get("path")
        if not isinstance(raw_path, str) or not raw_path.strip():
            return "irreversible"  # be conservative when we can't tell
        try:
            target = self._resolve(raw_path)
        except PermissionError:
            # Sandbox violation: run() raises before any I/O anyway.
            return "irreversible"
        if target.exists():
            # The agent edits its own proposals and notes without approval; a backup is kept.
            if self._is_own_proposal(target):
                return "reversible"
            return "irreversible"
        return "reversible"

    def _is_own_proposal(self, target: Path) -> bool:
        """True for the agent's own files under proposals/ or data/notes/."""
        resolved = target.resolve().parents
        return any((self.workspace_root / own).resolve() in resolved
                   for own in ("proposals", "data/notes"))

    # ------------------------------------------------------------------
    # execution
    # ------------------------------------------------------------------
    def run(self, path: str, content: str) -> dict[str, Any]:
        if not isinstance(path, str) or not path.strip():
            raise ValueError("path must be a non-empty string")
        if not isinstance(content, str):
            raise TypeError(
                f"content must be a string, got {type(content).__name__}"
            )

        from core.placeholder_text import (
            looks_like_unfilled_content,
            looks_like_unfilled_path,
        )

        # base_dir lets an existing file pass: overwriting a real file is never a template.
        if looks_like_unfilled_path(path, base_dir=str(self.workspace_root)):
            raise ValueError(
                f"refusing to write to an unfilled placeholder path: {path!r} — "
                "the plan carries a template where an address belongs"
            )
        if looks_like_unfilled_content(content):
            raise ValueError(
                "refusing to write an unfilled placeholder to disk: "
                f"{content.strip()!r}. Synthesize the real content first, "
                "then write it."
            )

        size = len(content.encode("utf-8"))
        if size > self.max_bytes:
            raise ValueError(
                f"content too large: {size} bytes > limit {self.max_bytes}"
            )

        # include_keywords=False: keyword hits only flagged mentions of keys in the agent's
        # own text; real key patterns are still refused.
        is_secret, reasons = contains_secret(content, include_keywords=False)
        if is_secret:
            # Only the kind of finding is reported, never the matched text.
            raise PermissionError(
                "refusing to write credentials to disk: " + ", ".join(reasons)
            )

        target = self._resolve(path)

        mode: Literal["create", "overwrite"]
        backup_rel: str | None = None
        target_existed_before = target.exists()
        if target_existed_before and target.stat().st_nlink > 1:
            raise PermissionError(f"refusing to overwrite {path!r}: it is hard-linked, the same bytes live elsewhere too")
        if target_existed_before:
            # Back up first, so an interrupted write cannot lose the old content.
            backup = _reserve_backup(target)
            try:
                shutil.copy2(target, backup)
            except BaseException:
                backup.unlink(missing_ok=True)  # an empty placeholder must not pass for a backup
                raise
            backup_rel = str(backup.relative_to(self.workspace_root))
            mode = "overwrite"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "create"

        # newline="": write as is (Windows would turn \n into \r\n); size is measured on disk.
        target.write_text(content, encoding="utf-8", newline="")
        size = target.stat().st_size

        target_rel = str(target.relative_to(self.workspace_root))
        # Undo plan for :rollback: delete a created file or restore an overwrite's backup.
        if target_existed_before:
            plan = CompensationPlan(
                tool_name=self.name,
                description=(
                    f"undo overwrite of '{target_rel}' by restoring "
                    f"backup '{backup_rel}'"
                ),
                actions=[
                    CompensationAction(
                        kind="restore_from_backup",
                        description=f"restore '{target_rel}' from '{backup_rel}'",
                        path=target_rel,
                        backup_path=backup_rel,
                    )
                ],
            )
        else:
            plan = CompensationPlan(
                tool_name=self.name,
                description=f"undo creation of '{target_rel}' by deleting it",
                actions=[
                    CompensationAction(
                        kind="delete_path_if_created",
                        description=f"delete '{target_rel}' created by file_write",
                        path=target_rel,
                    )
                ],
            )

        return {
            "path": target_rel,
            "mode": mode,
            "bytes_written": size,
            "backup_path": backup_rel,
            "compensation_plan": plan.to_dict(),
        }

    # ------------------------------------------------------------------
    # output contract
    # ------------------------------------------------------------------
    def validate_output(self, output: Any) -> tuple[bool, list[str]]:
        if not isinstance(output, dict):
            return False, [f"expected dict, got {type(output).__name__}"]

        issues: list[str] = []
        if not output.get("path"):
            issues.append("missing path")
        if output.get("mode") not in ("create", "overwrite"):
            issues.append(f"invalid mode: {output.get('mode')!r}")

        size = output.get("bytes_written")
        if not isinstance(size, int) or size < 0:
            issues.append("bytes_written must be a non-negative int")

        if output.get("mode") == "overwrite" and not output.get("backup_path"):
            issues.append("overwrite without backup_path")
        if output.get("mode") == "create" and output.get("backup_path"):
            issues.append("create must not have a backup_path")

        # Every successful write ships exactly one matching compensation action.
        plan = output.get("compensation_plan")
        if not isinstance(plan, dict):
            issues.append("missing or non-dict compensation_plan")
        else:
            actions = plan.get("actions") or []
            if not isinstance(actions, list) or len(actions) != 1:
                issues.append("compensation_plan must hold exactly one action")
            elif output.get("mode") == "create" and actions[0].get("kind") != "delete_path_if_created":
                issues.append(
                    "create mode must carry a delete_path_if_created action"
                )
            elif output.get("mode") == "overwrite" and actions[0].get("kind") != "restore_from_backup":
                issues.append(
                    "overwrite mode must carry a restore_from_backup action"
                )

        return (len(issues) == 0), issues
