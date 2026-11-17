"""Запретный файл остаётся запретным в любом написании пути, и список запретов один."""
from __future__ import annotations

from pathlib import Path

import pytest

import core.burn_in_supervisor as supervisor
from core.patch_route import _forbidden
from core.protected_paths import PROTECTED_FILES, PROTECTED_TREES, is_protected, path_key
from core.self_apply_lane import PROTECTED_CORE, FileChange, classify_patch_risk
from core.self_build_producer import _is_critical

_ALIASES = (
    "{d}/{n}", "./{d}/{n}", "{D}/{n}", "{d}\\{n}", "{d}/./{n}", "{d}/x/../{n}",
    "{d}//{n}", "{d}/{N}", "{d}/{n}.", "{d}/{n} ",
)


def _spellings(rel: str) -> list[str]:
    d, n = rel.rsplit("/", 1)
    return [a.format(d=d, n=n, D=d.capitalize(), N=n.upper()) for a in _ALIASES]


@pytest.mark.parametrize("path", _spellings("core/safe_vcs.py"))
def test_the_repair_route_blocks_every_spelling_of_a_protected_file(path: str) -> None:
    assert _forbidden([path]) == [path]


@pytest.mark.parametrize("path", ["../outside.py", "core/../../outside.py", "/etc/x.py",
                                  "C:/x.py", "core/drives.py::$DATA"])
def test_the_repair_route_refuses_a_path_that_leaves_the_repo(path: str) -> None:
    assert _forbidden([path]) == [path]


def test_the_repair_route_still_lets_ordinary_code_through() -> None:
    assert _forbidden(["core/drives.py", "./tests/test_new_witness.py", "core/x/../drives.py"]) == []


@pytest.mark.parametrize("path", ["App/main.py", ".\\cli\\repl.py", "Tools/web_fetch.py", "core/Approval_x.py"])
def test_the_repair_route_prefix_rules_see_the_normalised_path(path: str) -> None:
    assert _forbidden([path]) == [path]


def test_path_key_folds_spellings_and_refuses_escapes() -> None:
    assert {path_key(p) for p in _spellings("core/safe_vcs.py")} == {"core/safe_vcs.py"}
    assert path_key("../x") is None and path_key("a/../../x") is None
    assert path_key("") is None and path_key("C:\\x") is None


@pytest.mark.parametrize("rel", sorted(PROTECTED_FILES))
def test_all_four_walls_refuse_every_protected_file(rel: str) -> None:
    """Один список: что запретно одному пути самоправки, запретно всем четырём."""
    guard = supervisor._default_guard()
    for path in (rel, "./" + rel, rel.upper(), rel.replace("/", "\\")):
        assert is_protected(path)
        assert _forbidden([path]) == [path], path
        assert _is_critical(path), path
        assert supervisor._crosses_fence(path, guard), path
        assert classify_patch_risk([FileChange(path, "x = 1\n")])[0] is False, path


@pytest.mark.parametrize("tree", PROTECTED_TREES)
def test_all_four_walls_refuse_every_protected_tree(tree: str) -> None:
    guard = supervisor._default_guard()
    for path in (tree + "x.py", "./" + tree.upper() + "x.py", tree.rstrip("/")):
        assert _forbidden([path]) == [path], path
        assert _is_critical(path), path
        assert supervisor._crosses_fence(path, guard), path
        assert classify_patch_risk([FileChange(path, "x = 1\n")])[0] is False, path


def test_the_old_names_are_the_one_list() -> None:
    assert PROTECTED_CORE is PROTECTED_FILES
    assert supervisor.SUPERVISOR_FENCE is PROTECTED_FILES
    assert "core/protected_paths.py" in PROTECTED_FILES


def test_the_union_keeps_every_entry_of_the_four_former_lists() -> None:
    """Объединение держит записи прежних списков, кроме трёх, открытых полосе по диагнозу нарочно."""
    former = {
        # производитель самосборки (loop, model_usage и COGNITIVE_CORE остались только в его CRITICAL_DENY)
        "main.py", "core/autonomous_runtime.py",
        "core/safe_vcs.py", "core/self_build_producer.py",
        # принимающий burn-in
        "core/burn_in_supervisor.py", "core/burn_in_sandbox.py", "core/rule_approved_apply.py",
        "scripts/install_daemon.ps1", "state/burn_in_head.json",
        # полоса самоприменения
        "core/root_principles.py", "core/policy.py", "core/self_repair_utils.py",
        "tests/test_capability_baseline.py",
        # путь починки: тормоза оператора
        "data/budget_kill_switch.json", ".env", ".gitignore",
    }
    assert former <= PROTECTED_FILES


def test_a_trailing_dot_alias_of_a_protected_doc_is_not_low_risk() -> None:
    ok, _reason, rejected = classify_patch_risk([FileChange("docs/AGENT_DOCTRINE.md.", "x\n")])
    assert ok is False and rejected == ["docs/AGENT_DOCTRINE.md."]


def test_every_protected_core_module_exists() -> None:
    """Запись о несуществующем модуле выглядит охраной и не охраняет."""
    root = Path(__file__).resolve().parents[1]
    missing = sorted(p for p in PROTECTED_FILES
                     if p.startswith(("core/", "tools/", "scripts/")) and not (root / p).exists())
    assert missing == []


def test_proposals_folder_is_ignored_by_git() -> None:
    """Папка заявок самой полосы не должна делать дерево грязным для полосы."""
    lines = (Path(__file__).resolve().parents[1] / ".gitignore").read_text(encoding="utf-8").splitlines()
    assert "proposals/" in [line.strip() for line in lines]
