"""The architecture guard must hold on this tree — and must bite when it should.

A guard that cannot fail is decoration. Each invariant is therefore tested twice:
once against the real repository (it holds), once against a planted violation in
a synthetic tree (it is caught). The mutation tests are what keep the checks from
silently degrading into `return []`.
"""
from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
SCRIPT = REPO / "scripts" / "architecture_invariants.py"


def _load_module():
    spec = importlib.util.spec_from_file_location("architecture_invariants", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def guard():
    return _load_module()


# ── The real tree ────────────────────────────────────────────────────────────

def test_every_invariant_holds_on_this_repository(guard):
    assert guard.main() == 0


def test_core_layer_imports_downward_only(guard):
    problems, checked = guard.check_core_layering()
    assert problems == []
    assert checked > 100, "the check found almost no core modules — it is not looking"


def test_no_orphaned_core_modules(guard):
    problems, _ = guard.check_no_orphaned_modules()
    assert problems == []


def test_documented_env_flags_exist(guard):
    problems, checked = guard.check_documented_env_flags()
    assert problems == []
    assert checked > 10, "no AGENT_* flags were scanned — the check is vacuous"


def test_verifier_verdicts_are_all_bucketed(guard):
    problems, checked = guard.check_verdict_vocabulary()
    assert problems == []
    # dialogue_supported (issue #119) is one of them: the verdict that has to be
    # bucketed by every consumer or a supported claim counts as unsupported.
    assert checked >= 6


# ── The guard bites ──────────────────────────────────────────────────────────

def test_inv1_catches_a_core_module_importing_app(guard, tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "__init__.py").write_text("", encoding="utf-8")
    (core / "clean.py").write_text("from core.other import x\n", encoding="utf-8")
    (core / "leaky.py").write_text(
        "from app.single_instance import SingleInstanceLock\n", encoding="utf-8"
    )
    monkeypatch.setattr(guard, "CORE", core)

    problems, _ = guard.check_core_layering()

    assert len(problems) == 1
    assert "leaky.py" in problems[0]
    assert "may not depend on `app`" in problems[0]


def test_inv1_allows_relative_imports_inside_core(guard, tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "sibling.py").write_text("from .models import Thing\n", encoding="utf-8")
    monkeypatch.setattr(guard, "CORE", core)

    assert guard.check_core_layering()[0] == []


def test_inv2_catches_a_module_no_production_code_imports(
    guard, tmp_path, monkeypatch
):
    core = tmp_path / "core"
    core.mkdir()
    (core / "wired.py").write_text("x = 1\n", encoding="utf-8")
    (core / "stranded.py").write_text("x = 1\n", encoding="utf-8")
    cli = tmp_path / "cli"
    cli.mkdir()
    (cli / "app.py").write_text("from core.wired import x\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "CORE", core)
    monkeypatch.setattr(guard, "_PRODUCTION_ROOTS", ("core", "cli"))
    monkeypatch.setattr(guard, "_PRODUCTION_FILES", ())

    problems, _ = guard.check_no_orphaned_modules()

    assert len(problems) == 1
    assert "stranded.py" in problems[0]


def test_inv3_catches_a_documented_flag_no_code_reads(guard, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "guide.md").write_text(
        "Set `AGENT_GHOST_FLAG=on` to enable the thing.\n", encoding="utf-8"
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "real.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "_PRODUCTION_ROOTS", ("core",))
    monkeypatch.setattr(guard, "_PRODUCTION_FILES", ())

    problems, _ = guard.check_documented_env_flags()

    assert len(problems) == 1
    assert "AGENT_GHOST_FLAG" in problems[0]


def test_inv3_accepts_a_flag_the_document_calls_planned(guard, tmp_path, monkeypatch):
    docs = tmp_path / "docs"
    docs.mkdir()
    (docs / "plan.md").write_text(
        "Phase P1 (planned): master flag `AGENT_FUTURE_THING = off | on`.\n",
        encoding="utf-8",
    )
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "real.py").write_text("x = 1\n", encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "DOCS", docs)
    monkeypatch.setattr(guard, "_PRODUCTION_ROOTS", ("core",))
    monkeypatch.setattr(guard, "_PRODUCTION_FILES", ())

    assert guard.check_documented_env_flags()[0] == []


def test_inv4_catches_a_verdict_no_consumer_buckets(guard, tmp_path, monkeypatch):
    core = tmp_path / "core"
    core.mkdir()
    (core / "verifier_core.py").write_text(
        'verdict = "verified"\nverdict = "brand_new_verdict"\n', encoding="utf-8"
    )
    (core / "consumer.py").write_text('if verdict == "verified": pass\n', encoding="utf-8")
    monkeypatch.setattr(guard, "REPO", tmp_path)
    monkeypatch.setattr(guard, "CORE", core)
    monkeypatch.setattr(guard, "_VERDICT_CONSUMERS", ("core/consumer.py",))

    problems, _ = guard.check_verdict_vocabulary()

    assert len(problems) == 1
    assert "brand_new_verdict" in problems[0]
