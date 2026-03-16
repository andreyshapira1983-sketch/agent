"""
Tests for PolicyEngine: forbidden paths, quotas, check_apply_patch.
"""
from __future__ import annotations

import pytest

from src.governance.policy_engine import (
    PolicyEngine,
    check_action_allowed,
    check_quota,
    DEFAULT_FORBIDDEN_PREFIXES,
)


def test_policy_engine_forbidden_paths():
    engine = PolicyEngine()
    assert engine.is_path_allowed("tests/test_foo.py") is True
    assert engine.is_path_allowed("src/foo.py") is True
    assert engine.is_path_allowed(".cursor/rules/foo.mdc") is False
    assert engine.is_path_allowed(".git/config") is False
    assert engine.is_path_allowed("config/agent.json") is False
    assert engine.is_path_allowed("src/main.py") is False
    assert engine.is_path_allowed("src/hitl/audit_log.py") is False
    assert engine.is_path_allowed("src/governance/policy_engine.py") is False


def test_policy_engine_check_apply_patch():
    engine = PolicyEngine()
    allowed, msg = engine.check_apply_patch("tests/foo.py")
    assert allowed is True
    allowed, msg = engine.check_apply_patch("config/agent.json")
    assert allowed is False
    assert "protected" in msg.lower() or "forbidden" in msg.lower() or "config" in msg


def test_policy_engine_quota_cycle():
    engine = PolicyEngine(max_cycles=2, max_actions_per_cycle=3)
    assert engine.can_start_cycle() is True
    engine.record_cycle_done()
    engine.record_cycle_done()
    assert engine.can_start_cycle() is False


def test_policy_engine_quota_actions():
    engine = PolicyEngine(max_actions_per_cycle=2)
    engine.reset_cycle()
    assert engine.can_perform_action() is True
    engine.record_action()
    assert engine.can_perform_action() is True
    engine.record_action()
    assert engine.can_perform_action() is False


def test_check_action_allowed_apply_patch():
    allowed, _ = check_action_allowed("apply_patch", target_path="tests/x.py")
    assert allowed is True
    allowed, _ = check_action_allowed("apply_patch", target_path=".cursor/x")
    assert allowed is False


def test_check_quota():
    # Fresh engine allows action (cycle start time set in __init__)
    ok, _ = check_quota()
    assert ok is True


def test_check_quota_exhausted():
    engine = PolicyEngine(max_actions_per_cycle=1)
    engine.reset_cycle()
    engine.record_action()
    ok, msg = check_quota(engine)
    assert ok is False
    assert "quota" in msg.lower() or "limit" in msg.lower()


def test_check_action_allowed_apply_patch_no_path():
    allowed, msg = check_action_allowed("apply_patch", target_path=None)
    assert allowed is False
    assert "target_path" in msg


def test_check_action_allowed_run_tool():
    allowed, _ = check_action_allowed("run_tool")
    assert allowed is True


def test_check_action_allowed_unknown_kind():
    allowed, _ = check_action_allowed("unknown_kind")
    assert allowed is True


def test_policy_engine_record_action_and_cycle_done():
    engine = PolicyEngine(max_actions_per_cycle=2, max_cycles=1)
    engine.reset_cycle()
    engine.record_action()
    engine.record_action()
    assert engine.can_perform_action() is False
    engine.record_cycle_done()
    assert engine.can_start_cycle() is False


def test_policy_engine_restricted_tool():
    engine = PolicyEngine(restricted_tool_names=frozenset({"write_file", "dangerous_tool"}))
    allowed, _ = engine.check_run_tool("get_current_time", {})
    assert allowed is True
    allowed, msg = engine.check_run_tool("write_file", {"path": "x", "content": "y"})
    assert allowed is False
    assert "restricted" in msg.lower() or "write_file" in msg.lower()
    allowed, msg = engine.check_run_tool("dangerous_tool", {})
    assert allowed is False


def test_policy_engine_time_limit():
    import time as time_mod
    engine = PolicyEngine(max_cycle_time_sec=0.001, max_actions_per_cycle=10)
    engine.reset_cycle()
    time_mod.sleep(0.01)
    assert engine.can_perform_action() is False


def test_policy_engine_check_run_tool_quota_exhausted():
    engine = PolicyEngine(max_actions_per_cycle=1)
    engine.reset_cycle()
    engine.record_action()
    allowed, msg = engine.check_run_tool("any_tool", {})
    assert allowed is False
    assert "quota" in msg.lower() or "limit" in msg.lower()
