"""Tests for core.dependency_map — the project import/dependency map."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.dependency_map import (
    DependencyMap,
    ImporterInfo,
    _module_name_for,
    build_dependency_map,
)


@pytest.fixture()
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "tests").mkdir()
    (tmp_path / "core" / "__init__.py").write_text("", encoding="utf-8")
    (tmp_path / "core" / "target.py").write_text(
        "X = 1\n\n\ndef helper():\n    return X\n", encoding="utf-8"
    )
    return tmp_path


def test_module_name_for_paths():
    assert _module_name_for("core/verifier.py") == "core.verifier"
    assert _module_name_for("main.py") == "main"
    assert _module_name_for("core\\sub\\mod.py") == "core.sub.mod"
    assert _module_name_for("core/pkg/__init__.py") == "core.pkg"


def test_from_import_symbols_collected(workspace: Path):
    (workspace / "core" / "user.py").write_text(
        "from core.target import X, helper\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert [i.path for i in dep.importers] == ["core/user.py"]
    assert dep.importers[0].symbols == ["X", "helper"]
    assert dep.imported_symbols == {
        "X": ["core/user.py"],
        "helper": ["core/user.py"],
    }


def test_relative_import_resolved(workspace: Path):
    (workspace / "core" / "sibling.py").write_text(
        "from .target import helper\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert dep.imported_symbols.get("helper") == ["core/sibling.py"]


def test_plain_module_import_flagged(workspace: Path):
    (workspace / "core" / "consumer.py").write_text(
        "import core.target\n\nprint(core.target.X)\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert dep.importers[0].imports_module is True
    assert dep.importers[0].symbols == []


def test_related_tests_detected(workspace: Path):
    (workspace / "tests" / "test_target.py").write_text(
        "from core.target import helper\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert dep.related_tests == ["tests/test_target.py"]


def test_target_itself_and_unrelated_files_skipped(workspace: Path):
    (workspace / "core" / "other.py").write_text(
        "from core.elsewhere import thing\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert dep.importers == []
    assert dep.related_tests == []


def test_unparseable_file_is_skipped(workspace: Path):
    (workspace / "core" / "broken.py").write_text(
        "def broken(:\n", encoding="utf-8"
    )
    (workspace / "core" / "user.py").write_text(
        "from core.target import X\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert [i.path for i in dep.importers] == ["core/user.py"]


def test_skip_dirs_ignored(workspace: Path):
    hidden = workspace / "data"
    hidden.mkdir()
    (hidden / "notes.py").write_text(
        "from core.target import X\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    assert dep.importers == []


def test_builder_context_names_symbols_and_users(workspace: Path):
    (workspace / "core" / "user.py").write_text(
        "from core.target import X\n", encoding="utf-8"
    )
    (workspace / "tests" / "test_target.py").write_text(
        "from core.target import helper\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    ctx = dep.builder_context()
    assert "MUST remain importable" in ctx
    assert "X (used by core/user.py" in ctx
    assert "tests/test_target.py" in ctx


def test_builder_context_no_importers(workspace: Path):
    dep = build_dependency_map(workspace, "core/target.py")
    assert "no other project file imports" in dep.builder_context()


def test_summary_lines(workspace: Path):
    (workspace / "core" / "user.py").write_text(
        "from core.target import X\n", encoding="utf-8"
    )
    dep = build_dependency_map(workspace, "core/target.py")
    lines = dep.summary_lines()
    assert "importers=1" in lines
    assert any(line.startswith("imported_symbols=X") for line in lines)


def test_real_project_scan_finds_verifier_consumers():
    """Live sanity check against this very repository."""
    repo_root = Path(__file__).resolve().parents[1]
    dep = build_dependency_map(repo_root, "core/verifier.py")
    assert dep.importers, "expected real importers of core/verifier.py"
    all_symbols = dep.imported_symbols
    assert "verify" in all_symbols or any(
        i.imports_module for i in dep.importers
    )
