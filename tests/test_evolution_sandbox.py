"""Тесты sandbox: копия проекта, применение патча, pytest в sandbox."""
import tempfile
import pytest
from pathlib import Path

from src.evolution.sandbox import (
    create_sandbox,
    apply_in_sandbox,
    run_pytest_in_sandbox,
    cleanup_sandbox,
    run_in_sandbox,
)


def _project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def test_create_sandbox():
    root = _project_root()
    sandbox = create_sandbox(root)
    try:
        assert sandbox.exists() and sandbox.is_dir()
        assert (sandbox / "src").exists()
        assert (sandbox / "tests").exists()
        assert not (sandbox / ".git").exists()
    finally:
        cleanup_sandbox(sandbox)


def test_apply_in_sandbox():
    root = _project_root()
    sandbox = create_sandbox(root)
    try:
        apply_in_sandbox(sandbox, "tests/test_sandbox_dummy.txt", "hello")
        target = sandbox / "tests" / "test_sandbox_dummy.txt"
        assert target.read_text() == "hello"
    finally:
        if (sandbox / "tests" / "test_sandbox_dummy.txt").exists():
            (sandbox / "tests" / "test_sandbox_dummy.txt").unlink()
        cleanup_sandbox(sandbox)


def test_run_pytest_in_sandbox():
    root = _project_root()
    sandbox = create_sandbox(root)
    try:
        # Один быстрый файл тестов, чтобы не копировать полный прогон
        ok, out = run_pytest_in_sandbox(
            sandbox, timeout=60, test_path="tests/test_governance_policy_engine.py"
        )
        assert ok is True
        assert "passed" in out or "PASSED" in out or out
    finally:
        cleanup_sandbox(sandbox)


def test_run_in_sandbox_success():
    root = _project_root()
    # Применяем тот же контент к тестовому файлу — тесты не ломаются
    path = "tests/test_governance_policy_engine.py"
    content = (root / path).read_text(encoding="utf-8")
    ok, msg, sb = run_in_sandbox(
        root, path, content, timeout=60, test_path="tests/test_governance_policy_engine.py"
    )
    assert ok is True, msg
    assert sb is not None
    cleanup_sandbox(sb)


def test_cleanup_sandbox():
    root = _project_root()
    sandbox = create_sandbox(root)
    path = str(sandbox)
    cleanup_sandbox(sandbox)
    assert not Path(path).exists()


def test_incremental_sandbox_keeps_template_unchanged(monkeypatch, tmp_path):
    root = _project_root()
    monkeypatch.setenv("EVOLUTION_INCREMENTAL_SANDBOX", "1")
    monkeypatch.setattr(tempfile, "gettempdir", lambda: str(tmp_path))

    sandbox = create_sandbox(root)
    rel = "tests/test_governance_policy_engine.py"
    template_file = tmp_path / "agent_sandbox_cache" / "template" / rel
    original_template_text = template_file.read_text(encoding="utf-8")
    try:
        apply_in_sandbox(sandbox, rel, "# changed in sandbox\n")
        sandbox_text = (sandbox / rel).read_text(encoding="utf-8")
        assert sandbox_text == "# changed in sandbox\n"
        assert template_file.read_text(encoding="utf-8") == original_template_text
    finally:
        cleanup_sandbox(sandbox)
