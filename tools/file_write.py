"""File Write tool — sandboxed, secret-aware, backup-on-overwrite.

Risk model (§5 Action Risk & Reversibility):
  - Creating a NEW file inside the workspace  -> reversible
      The caller can simply delete it; no prior content was lost.
  - OVERWRITING an existing file              -> irreversible
      Even though we keep a timestamped `.bak.<ts>` copy, the operation
      crosses a trust boundary and must require human approval before
      the loop dispatches it. The backup is a recovery aid, not a
      reclassifier.
  - Path escapes the workspace                -> static risk falls back
      to `irreversible` (conservative) and `run()` raises PermissionError
      before touching the filesystem.

Hard rules enforced inside `run()` (defence in depth — even if the
policy gate is misconfigured, these still hold):
  - path must be a non-empty string
  - content must be a string
  - encoded content must be <= MAX_BYTES
  - content must NOT contain any high-confidence credential pattern
    (delegated to `core.secret_scanner.contains_secret`)
  - resolved path must stay within `workspace_root`

Output shape (consumed by `validate_output` and by the synthesizer):
    {
        "path":          str,         # workspace-relative
        "mode":          "create" | "overwrite",
        "bytes_written": int,
        "backup_path":   str | None,  # workspace-relative, set when mode="overwrite"
    }
"""
from __future__ import annotations

import shutil
import time
from pathlib import Path
from typing import Any, Literal

from core.compensation import CompensationAction, CompensationPlan
from core.secret_scanner import contains_secret
from tools.base import Risk, Tool, require_ascii_identifier


MAX_BYTES = 1 * 1024 * 1024  # 1 MiB


class FileWriteTool(Tool):
    name = "file_write"
    description = (
        "Write a UTF-8 text file inside the workspace. Creating a new "
        "file is reversible; overwriting an existing file is escalated "
        "to human approval and a timestamped backup is kept. Refuses "
        "to write paths outside the workspace or content that contains "
        "credentials."
    )
    # Conservative static fallback. The real per-call risk is computed
    # by `risk_for()` and is what PolicyGate reads.
    risk: Risk = "irreversible"

    def __init__(self, workspace_root: Path, max_bytes: int = MAX_BYTES):
        self.workspace_root = Path(workspace_root).resolve()
        self.max_bytes = max_bytes

    # ------------------------------------------------------------------
    # path resolution + sandbox
    # ------------------------------------------------------------------
    def _resolve(self, raw_path: str) -> Path:
        # ASCII-only identifier policy: file paths in this codebase are
        # programming identifiers, not human content. Cyrillic / other
        # non-ASCII is allowed inside `content`, never in `path`.
        require_ascii_identifier(raw_path, role="file_write path")
        candidate = (self.workspace_root / raw_path).resolve()
        try:
            candidate.relative_to(self.workspace_root)
        except ValueError as exc:
            raise PermissionError(
                f"Path escapes workspace: {candidate}"
            ) from exc
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
            # Sandbox violation: policy will see `irreversible`, but the
            # actual `run()` will raise before any I/O happens — defence
            # in depth.
            return "irreversible"
        if target.exists():
            return "irreversible"
        return "reversible"

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

        size = len(content.encode("utf-8"))
        if size > self.max_bytes:
            raise ValueError(
                f"content too large: {size} bytes > limit {self.max_bytes}"
            )

        is_secret, reasons = contains_secret(content)
        if is_secret:
            # Note: error message is itself kernel-redacted on the loop
            # side (TraceLogger + AgentLoop.run both apply redaction),
            # so leaking the matched literal here is safe — but we only
            # surface the *kind* of finding anyway.
            raise PermissionError(
                "refusing to write credentials to disk: " + ", ".join(reasons)
            )

        target = self._resolve(path)

        mode: Literal["create", "overwrite"]
        backup_rel: str | None = None
        target_existed_before = target.exists()
        if target_existed_before:
            # Backup first so the previous content is recoverable even if
            # the write that follows is interrupted (power loss, etc.).
            ts = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
            backup = target.with_suffix(target.suffix + f".bak.{ts}")
            shutil.copy2(target, backup)
            backup_rel = str(backup.relative_to(self.workspace_root))
            mode = "overwrite"
        else:
            target.parent.mkdir(parents=True, exist_ok=True)
            mode = "create"

        target.write_text(content, encoding="utf-8")

        target_rel = str(target.relative_to(self.workspace_root))
        # MVP-11 Compensation: tool ships an undo plan in its output.
        # The AgentLoop captures it after a successful tool_call so
        # `:rollback` can either delete the newly-created file (create
        # mode) or restore the backup we just wrote (overwrite mode).
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

        # MVP-11 contract: every successful write ships a compensation
        # plan (delete on create, restore_from_backup on overwrite).
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
