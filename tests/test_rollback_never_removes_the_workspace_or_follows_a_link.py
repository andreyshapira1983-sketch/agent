"""Rollback never deletes the workspace itself, and removes a symlink, not what it points to."""
from __future__ import annotations

import os
import pathlib

import pytest

from core.compensation import CompensationAction, CompensationPlan, _apply_action, apply_compensation_plan


def _workspace(tmp_path: pathlib.Path) -> pathlib.Path:
    ws = tmp_path / "ws"
    (ws / "data").mkdir(parents=True)
    (ws / "data" / "live.txt").write_text("keep", encoding="utf-8")
    (ws / "notes.txt").write_text("keep", encoding="utf-8")
    (ws / "work").mkdir()
    (ws / "work" / "result.txt").write_text("keep", encoding="utf-8")
    return ws


def _symlink(link: pathlib.Path, target: pathlib.Path) -> None:
    try:
        os.symlink(target, link, target_is_directory=target.is_dir())
    except (OSError, NotImplementedError) as exc:  # Windows without the symlink privilege
        pytest.skip(f"cannot create a symlink here: {exc}")


@pytest.mark.parametrize("path", [".", "", "./", "work/..", "data/..", "work/../."])
def test_a_path_that_resolves_to_the_workspace_root_is_refused(tmp_path, path: str) -> None:
    ws = _workspace(tmp_path)

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=path), ws)

    assert ws.is_dir(), f"rollback of {path!r} deleted the whole workspace"
    assert (ws / "notes.txt").exists() and (ws / "data" / "live.txt").exists()
    assert outcome.status == "error", f"refusal must be loud, got {outcome.status!r}"


def test_a_created_file_is_still_removed(tmp_path) -> None:
    ws = _workspace(tmp_path)

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="work/result.txt"), ws)

    assert outcome.status == "ok"
    assert not (ws / "work" / "result.txt").exists()
    assert (ws / "notes.txt").exists()


def test_a_created_directory_is_still_removed(tmp_path) -> None:
    ws = _workspace(tmp_path)

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="work"), ws)

    assert outcome.status == "ok"
    assert not (ws / "work").exists()
    assert (ws / "data" / "live.txt").exists()


def test_a_symlink_to_a_directory_removes_only_the_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _symlink(ws / "link", ws / "data")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="link"), ws)

    assert outcome.status == "ok"
    assert not os.path.lexists(ws / "link"), "the link itself must go"
    assert (ws / "data" / "live.txt").exists(), "rollback followed the link and deleted real data"


def test_a_symlink_to_the_workspace_root_removes_only_the_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _symlink(ws / "root_link", ws)

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="root_link"), ws)

    assert outcome.status == "ok"
    assert ws.is_dir() and (ws / "notes.txt").exists()
    assert not os.path.lexists(ws / "root_link")


def test_a_symlink_to_a_file_removes_only_the_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _symlink(ws / "file_link", ws / "notes.txt")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="file_link"), ws)

    assert outcome.status == "ok"
    assert not os.path.lexists(ws / "file_link")
    assert (ws / "notes.txt").read_text(encoding="utf-8") == "keep"


def test_a_link_that_lives_outside_the_workspace_is_refused(tmp_path) -> None:
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(outside / "back_in", ws / "work")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="../outside/back_in"), ws)

    assert outcome.status == "error"
    assert os.path.lexists(outside / "back_in"), "a link outside the workspace was removed"
    assert (ws / "work" / "result.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="Win32 collapses 'x\\..' and trailing dots before acting; POSIX resolves '..' after links")
@pytest.mark.parametrize("path", ["../outside/back_in/x/..", "../outside/back_in/...", "../outside/back_in/. "])
def test_a_windows_spelling_cannot_reach_a_link_outside(tmp_path, path: str) -> None:
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    _symlink(outside / "back_in", ws / "work")

    outcome = apply_compensation_plan(_plan(CompensationAction(kind="delete_path_if_created", path=path)), ws).outcomes[0]

    assert outcome.status == "error"
    assert os.path.lexists(outside / "back_in")
    assert (ws / "work" / "result.txt").exists()


def _plan(*actions: CompensationAction) -> CompensationPlan:
    return CompensationPlan(tool_name="test", actions=list(actions))


@pytest.mark.parametrize("path", ["data", "data/", "DATA"])
def test_a_protected_root_that_is_a_link_is_never_removed(tmp_path, path: str) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    store = tmp_path / "store"
    store.mkdir()
    (store / "live.txt").write_text("keep", encoding="utf-8")
    _symlink(ws / "data", store)

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=path), ws)

    if os.name != "nt" and path == "DATA":  # a case-sensitive POSIX filesystem has no entry named DATA: nothing to remove
        assert not os.path.lexists(ws / "DATA") or outcome.status == "error"
    else:
        assert outcome.status == "error"
    assert os.path.lexists(ws / "data"), "the protected root link was removed"
    assert (store / "live.txt").exists()


@pytest.mark.parametrize("plan_path", ["store", "store/data"])
def test_the_real_home_of_a_linked_protected_root_is_protected(tmp_path, plan_path: str) -> None:
    ws = tmp_path / "ws"
    (ws / "store" / "data").mkdir(parents=True)
    (ws / "store" / "data" / "live.txt").write_text("keep", encoding="utf-8")
    _symlink(ws / "data", ws / "store" / "data")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=plan_path), ws)

    assert outcome.status == "error"
    assert (ws / "store" / "data" / "live.txt").exists(), "rollback deleted the real home of data/"


@pytest.mark.parametrize("on_disk, plan_path", [("Logs", "Logs"), ("Logs", "logs"), ("DATA", "DATA"), ("Data", "data")])
def test_a_protected_root_in_another_case_is_never_removed(tmp_path, on_disk: str, plan_path: str) -> None:
    ws = tmp_path / "ws"
    (ws / on_disk).mkdir(parents=True)
    (ws / on_disk / "live.txt").write_text("keep", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=plan_path), ws)

    assert (ws / on_disk / "live.txt").exists(), f"rollback of {plan_path!r} deleted {on_disk}/"
    assert outcome.status in {"error", "noop"}


@pytest.mark.parametrize("plan_path", [".git/HEAD", ".git/objects", ".GIT/refs", ".git./HEAD"])
def test_nothing_under_git_is_ever_removed(tmp_path, plan_path: str) -> None:
    ws = tmp_path / "ws"
    for part in ("objects", "refs"):
        (ws / ".git" / part).mkdir(parents=True)
    (ws / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=plan_path), ws)

    assert outcome.status in {"error", "noop"}
    assert (ws / ".git" / "HEAD").exists() and (ws / ".git" / "objects").is_dir() and (ws / ".git" / "refs").is_dir()


@pytest.mark.parametrize("root_form", ["relative", "dotdot"])
def test_the_root_guard_does_not_depend_on_how_the_caller_spells_the_root(tmp_path, monkeypatch, root_form: str) -> None:
    ws = _workspace(tmp_path)
    monkeypatch.chdir(tmp_path)
    root = pathlib.Path("ws") if root_form == "relative" else tmp_path / "ws" / "work" / ".."

    for plan_path in (".", "", "work/.."):
        outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=plan_path), root)
        assert outcome.status == "error", f"{root_form} root, plan {plan_path!r}: {outcome.status}"
    assert (ws / "notes.txt").exists() and (ws / "data" / "live.txt").exists()


@pytest.mark.skipif(os.name != "nt", reason="directory junctions exist only on Windows")
def test_a_dangling_junction_is_removed_as_a_link(tmp_path) -> None:
    import _winapi

    ws = _workspace(tmp_path)
    gone = ws / "gone"
    gone.mkdir()
    _winapi.CreateJunction(str(gone), str(ws / "stale"))
    gone.rmdir()

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="stale"), ws)

    assert outcome.status == "ok"
    assert not os.path.lexists(ws / "stale")


def _restore(path: str, backup: str) -> CompensationAction:
    return CompensationAction(kind="restore_from_backup", path=path, backup_path=backup)


def test_restore_refuses_a_backup_that_is_a_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    (ws / "doc.txt").write_text("new", encoding="utf-8")
    _symlink(ws / "doc.txt.bak.20260101T000000Z", ws / "data" / "live.txt")

    outcome = _apply_action(_restore("doc.txt", "doc.txt.bak.20260101T000000Z"), ws)

    assert outcome.status == "error"
    assert (ws / "data" / "live.txt").read_text(encoding="utf-8") == "keep"


def test_restore_refuses_a_target_that_is_a_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _symlink(ws / "doc.txt", ws / "data" / "live.txt")
    (ws / "doc.txt.bak.20260101T000000Z").write_text("old", encoding="utf-8")

    outcome = _apply_action(_restore("doc.txt", "doc.txt.bak.20260101T000000Z"), ws)

    assert outcome.status == "error"
    assert (ws / "data" / "live.txt").read_text(encoding="utf-8") == "keep"


@pytest.mark.parametrize("target, backup", [
    ("data/live.txt", "notes.txt"),            # a backup that is not this target's
    (".", "work/result.txt"),                  # the workspace root as target
    ("work", "work.bak.20260101T000000Z"),     # a directory as target
    ("moved.txt", ".git/live.txt"),            # a backup under .git
])
def test_restore_refuses_plans_no_tool_writes(tmp_path, target: str, backup: str) -> None:
    ws = _workspace(tmp_path)
    (ws / ".git").mkdir()
    (ws / ".git" / "live.txt").write_text("keep", encoding="utf-8")
    (ws / "work.bak.20260101T000000Z").write_text("x", encoding="utf-8")
    before = {p: p.read_text(encoding="utf-8") for p in ws.rglob("*") if p.is_file()}

    outcome = _apply_action(_restore(target, backup), ws)

    assert outcome.status == "error", outcome.detail
    after = {p: p.read_text(encoding="utf-8") for p in ws.rglob("*") if p.is_file()}
    assert after == before, "a refused restore changed files"


def test_restore_still_works_for_a_real_file_write_backup(tmp_path) -> None:
    ws = _workspace(tmp_path)
    (ws / "doc.txt").write_text("new", encoding="utf-8")
    (ws / "doc.txt.bak.20260101T000000Z").write_text("old", encoding="utf-8")

    outcome = _apply_action(_restore("doc.txt", "doc.txt.bak.20260101T000000Z"), ws)

    assert outcome.status == "ok"
    assert (ws / "doc.txt").read_text(encoding="utf-8") == "old"


def test_shell_mkdir_through_a_dangling_link_rolls_back_what_it_created(tmp_path) -> None:
    from tools.shell_exec import ShellExecTool

    ws = _workspace(tmp_path)
    _symlink(ws / "latest", ws / "runs_2026")  # dangling until mkdir creates the target
    if not os.path.lexists(ws / "latest"):
        pytest.skip("dangling link could not be created")
    try:
        out = ShellExecTool(workspace_root=ws).run(["mkdir", "latest"])
    except (PermissionError, OSError) as exc:
        pytest.skip(f"this platform does not mkdir through a dangling link: {exc}")
    created = ws / "runs_2026"
    if not created.is_dir():
        pytest.skip("mkdir did not create the link target on this platform")

    report = apply_compensation_plan(CompensationPlan.from_dict(out["compensation_plan"]), ws)

    assert [o.status for o in report.outcomes] == ["ok"], report.summary()
    assert not created.exists(), "the directory the tool created was left behind"
    assert os.path.lexists(ws / "latest"), "rollback removed a link the tool did not create"


def test_a_created_directory_holding_a_git_repository_is_not_removed(tmp_path) -> None:
    ws = _workspace(tmp_path)
    (ws / "work" / "repo" / ".git").mkdir(parents=True)
    (ws / "work" / "repo" / ".git" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="work"), ws)

    assert outcome.status == "error"
    assert (ws / "work" / "repo" / ".git" / "HEAD").exists()


@pytest.mark.skipif(os.name == "nt", reason="these names are legal only outside Windows")
@pytest.mark.parametrize("name", ["report:v2.txt", "notes.", "draft "])
def test_ordinary_posix_names_still_roll_back(tmp_path, name: str) -> None:
    ws = _workspace(tmp_path)
    (ws / name).write_text("x", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=name), ws)

    assert outcome.status == "ok"
    assert not (ws / name).exists()


def test_a_failed_backup_copy_leaves_no_placeholder(tmp_path, monkeypatch) -> None:
    from tools.file_write import FileWriteTool

    ws = _workspace(tmp_path)
    (ws / "doc.txt").write_text("v0", encoding="utf-8")

    def broken_copy(*_args, **_kwargs):
        raise OSError("disk full")

    monkeypatch.setattr("tools.file_write.shutil.copy2", broken_copy)
    with pytest.raises(OSError):
        FileWriteTool(workspace_root=ws).run(path="doc.txt", content="v1")

    assert list(ws.glob("doc.txt.bak.*")) == [], "an empty placeholder would pass for a backup"
    assert (ws / "doc.txt").read_text(encoding="utf-8") == "v0"


def test_a_dangling_link_at_the_next_backup_name_is_skipped(tmp_path, monkeypatch) -> None:
    import time

    from tools.file_write import FileWriteTool

    frozen = time.gmtime(1_790_000_000)
    monkeypatch.setattr("tools.file_write.time.gmtime", lambda *_: frozen)
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (ws / "doc.txt").write_text("v0", encoding="utf-8")
    stamp = time.strftime("%Y%m%dT%H%M%SZ", frozen)
    _symlink(ws / f"doc.txt.bak.{stamp}", outside / "planted.txt")

    out = FileWriteTool(workspace_root=ws).run(path="doc.txt", content="v1")

    assert not (outside / "planted.txt").exists(), "the backup was written through a link outside the workspace"
    assert out["backup_path"] != f"doc.txt.bak.{stamp}"
    apply_compensation_plan(CompensationPlan.from_dict(out["compensation_plan"]), ws)
    assert (ws / "doc.txt").read_text(encoding="utf-8") == "v0"


def test_restore_refuses_to_write_through_a_hard_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "secret.txt").write_text("keep", encoding="utf-8")
    try:
        os.link(outside / "secret.txt", ws / "doc.txt")
    except OSError as exc:
        pytest.skip(f"no hard links here: {exc}")
    (ws / "doc.txt.bak.20260101T000000Z").write_text("planted", encoding="utf-8")

    outcome = _apply_action(_restore("doc.txt", "doc.txt.bak.20260101T000000Z"), ws)

    assert outcome.status == "error"
    assert (outside / "secret.txt").read_text(encoding="utf-8") == "keep"


def test_a_link_in_the_chain_of_a_protected_root_is_not_cut(tmp_path) -> None:
    ws = tmp_path / "ws"
    (ws / "store").mkdir(parents=True)
    (ws / "store" / "live.txt").write_text("keep", encoding="utf-8")
    _symlink(ws / "mid", ws / "store")
    _symlink(ws / "data", ws / "mid")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="mid"), ws)

    assert outcome.status == "error"
    assert os.path.lexists(ws / "mid") and (ws / "data" / "live.txt").exists()


def test_the_real_home_of_a_linked_git_is_protected(tmp_path) -> None:
    ws = tmp_path / "ws"
    (ws / "gitstore").mkdir(parents=True)
    (ws / "gitstore" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    _symlink(ws / ".git", ws / "gitstore")

    for plan_path in ("gitstore/HEAD", "gitstore"):
        outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=plan_path), ws)
        assert outcome.status == "error", plan_path
    assert (ws / "gitstore" / "HEAD").exists()


def test_the_real_home_named_by_a_gitdir_file_is_protected(tmp_path) -> None:
    ws = tmp_path / "ws"
    (ws / "store" / "repo").mkdir(parents=True)
    (ws / "store" / "repo" / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (ws / ".git").write_text("gitdir: store/repo\n", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="store/repo/HEAD"), ws)

    assert outcome.status == "error"
    assert (ws / "store" / "repo" / "HEAD").exists()


def test_an_operator_control_file_is_never_removed(tmp_path) -> None:
    from core.control_files import CONTROL_RELPATHS

    ws = _workspace(tmp_path)
    rel = sorted(CONTROL_RELPATHS)[0]
    (ws / rel).parent.mkdir(parents=True, exist_ok=True)
    (ws / rel).write_text("brake", encoding="utf-8")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path=rel), ws)

    assert outcome.status == "error"
    assert (ws / rel).read_text(encoding="utf-8") == "brake"


def test_restore_never_unlinks_the_real_home_of_a_protected_root(tmp_path) -> None:
    ws = _workspace(tmp_path)
    (ws / "doc.txt").write_text("new", encoding="utf-8")
    home = ws / "doc.txt.bak.20260101T000000Z"
    home.write_text("settings", encoding="utf-8")
    _symlink(ws / "config", home)

    outcome = _apply_action(_restore("doc.txt", "doc.txt.bak.20260101T000000Z"), ws)

    assert outcome.status == "error"
    assert home.read_text(encoding="utf-8") == "settings"


def test_a_detour_in_front_of_a_link_still_removes_only_the_link(tmp_path) -> None:
    ws = _workspace(tmp_path)
    _symlink(ws / "L", ws / "work")

    outcome = _apply_action(CompensationAction(kind="delete_path_if_created", path="missing/../L"), ws)

    assert (ws / "work" / "result.txt").exists(), "the link's target was deleted"
    assert outcome.status in {"ok", "error"}
    if outcome.status == "ok":
        assert not os.path.lexists(ws / "L")


def test_two_overwrites_in_one_second_keep_the_original_backup(tmp_path, monkeypatch) -> None:
    import time

    from tools.file_write import FileWriteTool

    frozen = time.gmtime(1_790_000_000)
    monkeypatch.setattr("tools.file_write.time.gmtime", lambda *_: frozen)
    ws = _workspace(tmp_path)
    (ws / "doc.txt").write_text("v0", encoding="utf-8")
    tool = FileWriteTool(workspace_root=ws)

    first = tool.run(path="doc.txt", content="v1")
    second = tool.run(path="doc.txt", content="v2")

    assert first["backup_path"] != second["backup_path"], "the second write reused the first backup name"
    for out in (second, first):  # LIFO, as :rollback does
        apply_compensation_plan(CompensationPlan.from_dict(out["compensation_plan"]), ws)
    assert (ws / "doc.txt").read_text(encoding="utf-8") == "v0", "the original content was lost"
