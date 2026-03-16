"""
Тесты для src/evolution/self_repair.py и EvolutionManager.generate_patch().
"""
from __future__ import annotations

import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from src.evolution.self_repair import _parse_first_failure, try_repair
from src.evolution.manager import EvolutionManager


# ---------------------------------------------------------------------------
# _parse_first_failure
# ---------------------------------------------------------------------------

def test_parse_first_failure_extracts_file() -> None:
    output = (
        "FAILED tests/test_foo.py::TestFoo::test_bar - AssertionError: boom\n"
        "E   AssertionError: boom\n"
    )
    result = _parse_first_failure(output)
    assert result is not None
    assert result["file"] == "tests/test_foo.py"


def test_parse_first_failure_returns_none_when_no_failed() -> None:
    result = _parse_first_failure("1 passed in 0.1s")
    assert result is None


# ---------------------------------------------------------------------------
# EvolutionManager.generate_patch — без ключа LLM → None
# ---------------------------------------------------------------------------

def test_generate_patch_no_api_key_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_KEY_API", raising=False)

    mgr = EvolutionManager(
        patch_directory=str(tmp_path / "patches"),
        test_directory=str(tmp_path / "tests"),
        backup_directory=str(tmp_path / "backups"),
    )
    # Используем путь к реальному файлу внутри проекта — path-guard пройдёт,
    # но ключа нет → функция вернёт None ещё до вызова LLM.
    result = mgr.generate_patch(
        {"file": "src/evolution/manager.py", "error": "NameError", "traceback": ""}
    )
    assert result is None


def test_generate_patch_missing_file_returns_none(tmp_path, monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_KEY_API", raising=False)

    mgr = EvolutionManager(
        patch_directory=str(tmp_path / "patches"),
        test_directory=str(tmp_path),
        backup_directory=str(tmp_path / "backups"),
    )
    result = mgr.generate_patch({"file": "nonexistent/path.py", "error": "E", "traceback": ""})
    assert result is None


def test_generate_patch_no_file_key_returns_none(tmp_path) -> None:
    mgr = EvolutionManager(
        patch_directory=str(tmp_path),
        test_directory=str(tmp_path),
        backup_directory=str(tmp_path / "backups"),
    )
    result = mgr.generate_patch({})
    assert result is None


# ---------------------------------------------------------------------------
# EvolutionManager.generate_patch — с мокнутым OpenAI → возвращает patch_id
# ---------------------------------------------------------------------------

def test_generate_patch_with_mocked_llm_returns_patch_id(monkeypatch) -> None:
    """
    При наличии ключа и мокнутом OpenAI+submit — generate_patch
    возвращает строку patch_id.
    """
    import sys, types

    monkeypatch.setenv("OPENAI_API_KEY", "sk-test")

    fake_patch_id = "20260316_120000_src_evolution_manager_py"

    fake_choice = SimpleNamespace(message=SimpleNamespace(content="def foo(): pass\n"))
    fake_response = SimpleNamespace(choices=[fake_choice])
    mock_client = MagicMock()
    mock_client.chat.completions.create.return_value = fake_response
    mock_openai_cls = MagicMock(return_value=mock_client)

    # Инъекция мок-модуля openai в sys.modules
    fake_openai_mod = types.ModuleType("openai")
    fake_openai_mod.OpenAI = mock_openai_cls  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "openai", fake_openai_mod)

    # Мокаем submit_candidate_patch чтобы не трогать реальную FS
    import src.evolution.safety as safety_mod
    monkeypatch.setattr(safety_mod, "submit_candidate_patch", lambda *_a, **_kw: fake_patch_id)

    # target — существующий файл внутри проекта (path-guard пройдёт)
    from pathlib import Path
    mgr = EvolutionManager(
        patch_directory=str(Path("config/candidate_patches")),
        test_directory="tests",
        backup_directory="backups",
    )
    result = mgr.generate_patch({
        "file": "src/evolution/manager.py",
        "error": "NameError: boom",
        "traceback": "Traceback (most recent call last):\n  ...",
    })
    assert result == fake_patch_id


# ---------------------------------------------------------------------------
# try_repair — тесты зелёные → True без LLM
# ---------------------------------------------------------------------------

def test_try_repair_returns_true_when_tests_pass(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.evolution.self_repair._run_pytest",
        lambda: (True, "1 passed"),
    )
    assert try_repair() is True


def test_try_repair_returns_false_when_no_failure_parseable(monkeypatch) -> None:
    monkeypatch.setattr(
        "src.evolution.self_repair._run_pytest",
        lambda: (False, "some weird output without FAILED marker"),
    )
    assert try_repair() is False


def test_try_repair_returns_false_when_no_llm_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("OPEN_KEY_API", raising=False)

    output = (
        "FAILED tests/test_x.py::test_broken - AssertionError\n"
        "E   AssertionError\n"
    )
    monkeypatch.setattr("src.evolution.self_repair._run_pytest", lambda: (False, output))
    # generate_patch вернёт None без ключа → try_repair → False
    assert try_repair() is False
