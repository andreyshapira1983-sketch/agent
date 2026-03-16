from __future__ import annotations

import json

from src.tools.impl import self_model_tools
from src.tools.registry import call


self_model_tools.register_self_model_tools()


def test_generate_module_skeleton_on_exists_notify(tmp_path, monkeypatch):
    root = tmp_path
    (root / "templates" / "module").mkdir(parents=True, exist_ok=True)
    (root / "templates" / "module" / "module.py").write_text(
        "def {{module}}():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning" / "alpha.py").write_text("def alpha():\n    return 'x'\n", encoding="utf-8")

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    out = call(
        "generate_module_skeleton",
        system="learning",
        module_name="alpha",
        module_type="module",
        on_exists="notify",
    )
    assert "File already exists" in out


def test_generate_module_skeleton_on_exists_suffix_new(tmp_path, monkeypatch):
    root = tmp_path
    (root / "templates" / "module").mkdir(parents=True, exist_ok=True)
    (root / "templates" / "module" / "module.py").write_text(
        "def {{module}}():\n    return 'ok'\n",
        encoding="utf-8",
    )
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning" / "alpha.py").write_text("def alpha():\n    return 'x'\n", encoding="utf-8")

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    out = call(
        "generate_module_skeleton",
        system="learning",
        module_name="alpha",
        module_type="module",
        on_exists="suffix_new",
    )
    assert "Created src/learning/alpha_new.py" in out
    created = (root / "src" / "learning" / "alpha_new.py").read_text(encoding="utf-8")
    assert "def alpha_new()" in created


def test_validate_project_layout_reports_duplicates_and_nesting(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "a").mkdir(parents=True, exist_ok=True)
    (root / "src" / "b").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "a").mkdir(parents=True, exist_ok=True)

    (root / "src" / "a" / "mod.py").write_text("def mod():\n    return 1\n", encoding="utf-8")
    (root / "src" / "b" / "mod.py").write_text("def mod():\n    return 2\n", encoding="utf-8")

    (root / "tests" / "test_mod.py").write_text("def test_same():\n    assert True\n", encoding="utf-8")
    (root / "tests" / "a" / "test_mod.py").write_text("def test_same():\n    assert True\n", encoding="utf-8")

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    raw = call("validate_project_layout", include_success_logs=False)
    data = json.loads(raw)

    assert data["ok"] is False
    assert data["counts"]["duplicate_src_filenames"] >= 1
    assert data["counts"]["duplicate_test_filenames"] >= 1
    assert data["counts"]["duplicate_test_names"] >= 1
    assert any(item.get("type") == "missing_test_dir" and item.get("dir") == "b" for item in data["nesting_issues"])


def test_validate_project_layout_main_symbol_check_multiple_expected(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "learning").mkdir(parents=True, exist_ok=True)

    (root / "src" / "learning" / "alpha_beta.py").write_text(
        "class AlphaBeta:\n    pass\n\n"
        "def build():\n    return AlphaBeta()\n",
        encoding="utf-8",
    )
    (root / "tests" / "learning" / "test_alpha_beta.py").write_text(
        "def test_smoke():\n    assert True\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    raw = call(
        "validate_project_layout",
        expected_symbols=["AlphaBeta", "build"],
        include_success_logs=True,
    )
    data = json.loads(raw)

    assert data["counts"]["symbol_issues"] == 0
    assert data["ok"] is True
    assert any("Main symbol check passed" in line for line in data["trace"])


def test_validate_project_layout_symbol_modes(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning" / "alpha_beta.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    strict_raw = call("validate_project_layout", include_success_logs=False, symbol_check_mode="strict")
    strict_data = json.loads(strict_raw)
    assert strict_data["counts"]["symbol_issues"] == 1

    relaxed_raw = call("validate_project_layout", include_success_logs=False, symbol_check_mode="relaxed")
    relaxed_data = json.loads(relaxed_raw)
    assert relaxed_data["counts"]["symbol_issues"] == 0

    off_raw = call("validate_project_layout", include_success_logs=False, symbol_check_mode="off")
    off_data = json.loads(off_raw)
    assert off_data["counts"]["symbol_checks"] == 0
    assert off_data["counts"]["symbol_issues"] == 0


def test_validate_project_layout_symbol_whitelist(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning" / "alpha_beta.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    raw = call(
        "validate_project_layout",
        include_success_logs=False,
        symbol_check_mode="strict",
        symbol_check_whitelist=["src/learning/*"],
    )
    data = json.loads(raw)
    assert data["counts"]["symbol_checks"] == 0
    assert data["counts"]["symbol_issues"] == 0


def test_validate_project_layout_writes_summary_top_issues(tmp_path, monkeypatch):
    root = tmp_path
    (root / "src" / "a").mkdir(parents=True, exist_ok=True)
    (root / "src" / "b").mkdir(parents=True, exist_ok=True)
    (root / "tests" / "a").mkdir(parents=True, exist_ok=True)

    (root / "src" / "a" / "mod.py").write_text("def mod():\n    return 1\n", encoding="utf-8")
    (root / "src" / "b" / "mod.py").write_text("def mod():\n    return 2\n", encoding="utf-8")
    (root / "tests" / "test_mod.py").write_text("def test_same():\n    assert True\n", encoding="utf-8")
    (root / "tests" / "a" / "test_mod.py").write_text("def test_same():\n    assert True\n", encoding="utf-8")

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    summary_rel = "test-results/custom_summary.json"
    raw = call(
        "validate_project_layout",
        include_success_logs=False,
        summary_path=summary_rel,
        summary_top_n=2,
    )
    data = json.loads(raw)

    assert data["summary_file"] == summary_rel
    summary_file = root / summary_rel
    assert summary_file.exists()
    summary_data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert "top_issues" in summary_data
    assert len(summary_data["top_issues"]) == 2


def test_validate_project_layout_uses_defaults_from_config(tmp_path, monkeypatch):
    root = tmp_path
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "config" / "layout_validation.json").write_text(
        json.dumps(
            {
                "symbol_check_mode": "off",
                "symbol_check_whitelist": ["src/learning/*"],
                "summary_path": "test-results/default_summary.json",
                "summary_top_n": 3,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (root / "src" / "learning" / "alpha_beta.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    raw = call("validate_project_layout", include_success_logs=False)
    data = json.loads(raw)
    assert data["symbol_check_mode"] == "off"
    assert data["counts"]["symbol_checks"] == 0
    assert data["summary_file"] == "test-results/default_summary.json"
    summary_file = root / "test-results" / "default_summary.json"
    assert summary_file.exists()
    summary_data = json.loads(summary_file.read_text(encoding="utf-8"))
    assert len(summary_data.get("top_issues") or []) <= 3


def test_validate_project_layout_uses_profile_from_layout_config(tmp_path, monkeypatch):
    root = tmp_path
    (root / "config").mkdir(parents=True, exist_ok=True)
    (root / "src" / "learning").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(parents=True, exist_ok=True)

    (root / "config" / "layout_validation.json").write_text(
        json.dumps(
            {
                "active_profile": "canary",
                "symbol_check_mode": "strict",
                "summary_path": "test-results/base.json",
                "profiles": {
                    "canary": {
                        "symbol_check_mode": "relaxed",
                        "symbol_check_whitelist": ["src/learning/*"],
                        "summary_path": "test-results/from_profile.json",
                        "summary_top_n": 5,
                    }
                },
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    (root / "src" / "learning" / "alpha_beta.py").write_text(
        "def helper():\n    return 1\n",
        encoding="utf-8",
    )

    monkeypatch.setattr(self_model_tools, "_project_root", lambda: root)

    raw = call("validate_project_layout", include_success_logs=False)
    data = json.loads(raw)

    assert data["symbol_check_mode"] == "relaxed"
    assert data["counts"]["symbol_checks"] == 0
    assert data["summary_file"] == "test-results/from_profile.json"
