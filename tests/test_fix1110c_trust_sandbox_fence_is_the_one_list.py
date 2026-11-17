"""The sandbox refuses by the one protected list, in every spelling of the path."""
from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from core.burn_in_sandbox import _FENCE, sandbox_execution_verdict
from core.protected_paths import PROTECTED_FILES, PROTECTED_TREES


def _verdict(workspace: Path, rel: str) -> tuple[bool, str]:
    proposal = SimpleNamespace(files=(SimpleNamespace(path=rel),))
    return sandbox_execution_verdict(proposal, workspace=workspace)


def test_the_sandbox_list_is_inside_the_one_list() -> None:
    assert _FENCE <= PROTECTED_FILES


@pytest.mark.parametrize("rel", sorted(_FENCE))
def test_a_fence_file_is_named_as_the_fence_in_every_spelling(tmp_path: Path, rel: str) -> None:
    """The same spellings `core.protected_paths.path_key` folds, the trailing dot included."""
    for path in (rel, "./" + rel, rel.upper(), rel.replace("/", "\\"), rel + ".", rel + " "):
        allowed, reason = _verdict(tmp_path, path)
        assert not allowed and "fence" in reason, (path, reason)


@pytest.mark.parametrize("rel", sorted(PROTECTED_FILES))
def test_every_protected_file_is_refused_in_every_spelling(tmp_path: Path, rel: str) -> None:
    d, _, n = rel.rpartition("/")
    spellings = [rel, "./" + rel, rel.upper(), rel.replace("/", "\\"), rel + ".", rel + " "]
    if d:
        spellings += [f"{d}/x/../{n}", f"{d}//{n}"]
    for path in spellings:
        assert not _verdict(tmp_path, path)[0], path


@pytest.mark.parametrize("tree", PROTECTED_TREES)
def test_every_protected_tree_is_refused(tmp_path: Path, tree: str) -> None:
    for path in (tree + "x.py", tree.upper() + "x.py"):
        assert not _verdict(tmp_path, path)[0], path


def test_ordinary_code_is_still_allowed(tmp_path: Path) -> None:
    allowed, reason = _verdict(tmp_path, "core/drives.py")
    assert allowed, reason
