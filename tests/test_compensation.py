"""Compensation system unit tests (§5 Undo — first appearance in MVP-11).

Three properties are pinned per action kind:
  (a) the action does what it claims when prerequisites hold
  (b) it is idempotent — a second apply is a `noop`, never an error
  (c) it is sandboxed — paths that escape the workspace fail with
      `status='error'` and never touch the filesystem
"""
from __future__ import annotations

from pathlib import Path

import pytest

from core.compensation import (
    CompensationAction,
    CompensationOutcome,
    CompensationPlan,
    CompensationReport,
    apply_compensation_plan,
)


# ===========================================================
# Plan / Action / serialisation
# ===========================================================

class TestPlanShape:
    def test_default_plan_has_unique_id(self):
        a = CompensationPlan()
        b = CompensationPlan()
        assert a.id != b.id
        assert a.id.startswith("comp_")

    def test_noop_helper(self):
        plan = CompensationPlan.noop(tool_name="shell_exec", description="hi")
        assert plan.tool_name == "shell_exec"
        assert len(plan.actions) == 1
        assert plan.actions[0].kind == "noop"

    def test_to_dict_roundtrip(self):
        plan = CompensationPlan(
            tool_name="shell_exec",
            description="undo touch foo",
            actions=[
                CompensationAction(
                    kind="delete_path_if_created", path="foo", description="d"
                )
            ],
        )
        d = plan.to_dict()
        # JSON-safe primitive types only.
        assert isinstance(d["id"], str)
        assert isinstance(d["created_at"], str)
        assert d["actions"][0]["kind"] == "delete_path_if_created"

        rebuilt = CompensationPlan.from_dict(d)
        assert rebuilt.id == plan.id
        assert rebuilt.tool_name == plan.tool_name
        assert rebuilt.description == plan.description
        assert rebuilt.actions[0].kind == plan.actions[0].kind
        assert rebuilt.actions[0].path == plan.actions[0].path

    def test_from_dict_missing_id_gets_one(self):
        rebuilt = CompensationPlan.from_dict({"tool_name": "shell_exec"})
        assert rebuilt.id.startswith("comp_")


# ===========================================================
# delete_path_if_created
# ===========================================================

class TestDeletePathAction:
    def test_removes_existing_file(self, workspace: Path):
        target = workspace / "created.txt"
        target.write_text("by the tool", encoding="utf-8")
        plan = CompensationPlan(
            actions=[
                CompensationAction(kind="delete_path_if_created", path="created.txt")
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert isinstance(report, CompensationReport)
        assert len(report.outcomes) == 1
        assert report.outcomes[0].status == "ok"
        assert not target.exists()

    def test_removes_existing_directory_recursively(self, workspace: Path):
        target = workspace / "newdir"
        (target / "nested").mkdir(parents=True)
        (target / "nested" / "f.txt").write_text("x", encoding="utf-8")
        plan = CompensationPlan(
            actions=[CompensationAction(kind="delete_path_if_created", path="newdir")]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "ok"
        assert not target.exists()

    def test_idempotent_when_already_gone(self, workspace: Path):
        plan = CompensationPlan(
            actions=[
                CompensationAction(kind="delete_path_if_created", path="never_existed")
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "noop"
        assert "already absent" in report.outcomes[0].detail

    def test_path_escape_rejected_even_on_rollback(self, workspace: Path):
        # An attacker who managed to get a malformed plan onto the log
        # still can't delete outside the workspace at apply time.
        outside_target = workspace.parent / "evil_outside.txt"
        outside_target.write_text("must survive", encoding="utf-8")
        plan = CompensationPlan(
            actions=[
                CompensationAction(
                    kind="delete_path_if_created", path="../evil_outside.txt"
                )
            ]
        )
        try:
            report = apply_compensation_plan(plan, workspace)
            assert report.outcomes[0].status == "error"
            assert "escapes workspace" in report.outcomes[0].detail
            # The file outside the workspace is untouched.
            assert outside_target.exists()
            assert outside_target.read_text(encoding="utf-8") == "must survive"
        finally:
            outside_target.unlink(missing_ok=True)


# ===========================================================
# restore_from_backup
# ===========================================================

class TestRestoreFromBackupAction:
    def test_restores_overwritten_file_and_removes_backup(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("new content", encoding="utf-8")
        backup = workspace / "doc.txt.bak.20260525T120000Z"
        backup.write_text("ORIGINAL", encoding="utf-8")

        plan = CompensationPlan(
            actions=[
                CompensationAction(
                    kind="restore_from_backup",
                    path="doc.txt",
                    backup_path="doc.txt.bak.20260525T120000Z",
                )
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "ok"
        assert target.read_text(encoding="utf-8") == "ORIGINAL"
        # Backup file is consumed.
        assert not backup.exists()

    def test_idempotent_when_backup_already_gone(self, workspace: Path):
        plan = CompensationPlan(
            actions=[
                CompensationAction(
                    kind="restore_from_backup",
                    path="any.txt",
                    backup_path="any.txt.bak.20260525T120000Z",
                )
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "noop"
        assert "backup already gone" in report.outcomes[0].detail

    def test_backup_path_escape_rejected(self, workspace: Path):
        target = workspace / "doc.txt"
        target.write_text("victim", encoding="utf-8")
        plan = CompensationPlan(
            actions=[
                CompensationAction(
                    kind="restore_from_backup",
                    path="doc.txt",
                    backup_path="../../etc/passwd",
                )
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "error"
        # Target untouched.
        assert target.read_text(encoding="utf-8") == "victim"


# ===========================================================
# Plan with multiple actions — LIFO order
# ===========================================================

class TestMultiActionPlan:
    def test_actions_applied_in_reverse_order(self, workspace: Path):
        # Plan created two paths: a/b/c.txt then x.txt. Compensation
        # must remove them in reverse — x.txt first, then a/b/c.txt.
        (workspace / "a" / "b").mkdir(parents=True)
        (workspace / "a" / "b" / "c.txt").write_text("y", encoding="utf-8")
        (workspace / "x.txt").write_text("z", encoding="utf-8")

        plan = CompensationPlan(
            actions=[
                CompensationAction(kind="delete_path_if_created", path="a/b/c.txt"),
                CompensationAction(kind="delete_path_if_created", path="x.txt"),
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        # outcomes[0] is the LAST action applied first (reverse order).
        assert report.outcomes[0].action.path == "x.txt"
        assert report.outcomes[1].action.path == "a/b/c.txt"
        assert all(o.status == "ok" for o in report.outcomes)
        assert not (workspace / "x.txt").exists()
        assert not (workspace / "a" / "b" / "c.txt").exists()

    def test_one_failing_action_does_not_abort_the_rest(self, workspace: Path):
        # First action targets a path outside the workspace (will error).
        # Second action targets a real file inside the workspace (ok).
        (workspace / "real.txt").write_text("y", encoding="utf-8")
        plan = CompensationPlan(
            actions=[
                CompensationAction(kind="delete_path_if_created", path="../evil"),
                CompensationAction(kind="delete_path_if_created", path="real.txt"),
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        assert len(report.outcomes) == 2
        # Reversed order: 'real.txt' applied FIRST, '../evil' SECOND.
        assert report.outcomes[0].action.path == "real.txt"
        assert report.outcomes[0].status == "ok"
        assert report.outcomes[1].action.path == "../evil"
        assert report.outcomes[1].status == "error"

    def test_noop_action_alone(self, workspace: Path):
        plan = CompensationPlan(
            actions=[CompensationAction(kind="noop", description="nothing to undo")]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "noop"

    def test_unknown_action_kind_is_error_not_crash(self, workspace: Path):
        plan = CompensationPlan(
            actions=[CompensationAction(kind="bogus_kind")]  # type: ignore[arg-type]
        )
        report = apply_compensation_plan(plan, workspace)
        assert report.outcomes[0].status == "error"
        assert "unknown action kind" in report.outcomes[0].detail


# ===========================================================
# Report summary
# ===========================================================

class TestReportSummary:
    def test_counts_per_status(self, workspace: Path):
        (workspace / "a.txt").write_text("a", encoding="utf-8")
        plan = CompensationPlan(
            actions=[
                CompensationAction(kind="delete_path_if_created", path="a.txt"),
                CompensationAction(kind="delete_path_if_created", path="missing.txt"),
                CompensationAction(kind="delete_path_if_created", path="../escape"),
            ]
        )
        report = apply_compensation_plan(plan, workspace)
        s = report.summary()
        assert s["ok"] == 1
        assert s["noop"] == 1
        assert s["error"] == 1
        assert s["action_count"] == 3


# ===========================================================
# Last-defence catch — _apply_action raising unexpectedly
# ===========================================================

class TestLastDefence:
    def test_unexpected_exception_surfaces_as_error_outcome(
        self, workspace: Path, monkeypatch
    ):
        """If a future bug makes `_apply_action` raise something not
        caught locally, the outer `apply_compensation_plan` must record
        an `error` outcome for that action and continue with the rest."""
        import core.compensation as comp

        good_target = workspace / "good.txt"
        good_target.write_text("g", encoding="utf-8")

        plan = CompensationPlan(
            actions=[
                # First action will succeed.
                CompensationAction(kind="delete_path_if_created", path="good.txt"),
                # Second action is going to raise via monkeypatched dispatcher.
                CompensationAction(kind="delete_path_if_created", path="boom.txt"),
            ]
        )

        real_apply = comp._apply_action

        def maybe_explode(action, root):
            if action.path == "boom.txt":
                raise RuntimeError("simulated unexpected failure")
            return real_apply(action, root)

        monkeypatch.setattr(comp, "_apply_action", maybe_explode)

        report = comp.apply_compensation_plan(plan, workspace)
        assert len(report.outcomes) == 2
        # Reversed order: 'boom.txt' applied FIRST, 'good.txt' SECOND.
        assert report.outcomes[0].action.path == "boom.txt"
        assert report.outcomes[0].status == "error"
        assert "RuntimeError" in report.outcomes[0].detail
        assert "simulated unexpected" in report.outcomes[0].detail
        # The good action still ran.
        assert report.outcomes[1].status == "ok"
        assert not good_target.exists()
