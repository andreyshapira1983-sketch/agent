"""Tests for core.self_build_rules — rollback-to-hard-rule learning."""
from __future__ import annotations

from pathlib import Path

from core.self_build_rules import (
    Rule,
    RuleStore,
    default_rules_path,
    record_rules_from_result,
)


def _rolled_back(reason: str, proposal_id: str = "ain_test123") -> dict:
    return {"status": "rolled_back", "reason": reason, "proposal_id": proposal_id}


def test_rule_store_roundtrip_and_dedup(tmp_path: Path):
    store = RuleStore(tmp_path / "rules.jsonl")
    rule = Rule(
        target="core/x.py", kind="keep_importable", symbol="A", source="test"
    )
    assert store.add(rule) is True
    assert store.add(rule) is False  # duplicate
    loaded = store.load()
    assert len(loaded) == 1
    assert loaded[0].symbol == "A"
    assert store.rules_for("core/x.py")[0].target == "core/x.py"
    assert store.rules_for("core/other.py") == []


def test_rule_store_skips_malformed_lines(tmp_path: Path):
    path = tmp_path / "rules.jsonl"
    path.write_text(
        'not json\n{"target": "", "kind": "keep_importable", "symbol": "A"}\n'
        '{"target": "core/x.py", "kind": "keep_importable", "symbol": "B", '
        '"source": "s"}\n',
        encoding="utf-8",
    )
    loaded = RuleStore(path).load()
    assert [r.symbol for r in loaded] == ["B"]


def test_record_rules_from_result_persists(tmp_path: Path):
    result = _rolled_back(
        "ImportError: cannot import name 'helper' from 'core.util_mod'"
    )
    added = record_rules_from_result(tmp_path, result)
    assert added == 1
    store = RuleStore(default_rules_path(tmp_path))
    assert store.rules_for("core/util_mod.py")[0].symbol == "helper"
    # same result again -> dedup, nothing added
    assert record_rules_from_result(tmp_path, result) == 0


# ── critic enforcement ───────────────────────────────────────────────────────


def _build(content: str, target: str = "core/x.py") -> dict:
    return {
        "content": content,
        "files": [{"path": target, "content": content}],
        "test_paths": ["tests"],
        "test_pattern": None,
        "reason": "change",
        "confidence": 0.9,
    }


def test_critic_vetoes_hard_rule_violation():
    from core.self_build_producer import _critic_review

    old = "A = 1\n\n\ndef keep():\n    return A\n"
    new = "def keep():\n    return 1\n"  # drops A entirely
    out = _critic_review(
        "core/x.py",
        old,
        _build(new),
        confidence_threshold=0.6,
        required_symbols={"A": "rollback ain_1: ImportError"},
    )
    assert out.decision == "veto"
    joined = " | ".join(out.data["veto_reasons"])
    assert "HARD RULE violated" in joined
    assert "A must remain importable" in joined


def test_critic_passes_when_rule_satisfied_by_definition():
    from core.self_build_producer import _critic_review

    old = "A = 1\n\n\ndef keep():\n    return A\n"
    new = "A = 2\n\n\ndef keep():\n    return A\n"
    out = _critic_review(
        "core/x.py",
        old,
        _build(new),
        confidence_threshold=0.6,
        required_symbols={"A": "rollback ain_1: ImportError"},
    )
    assert out.decision == "pass", out.data["veto_reasons"]
