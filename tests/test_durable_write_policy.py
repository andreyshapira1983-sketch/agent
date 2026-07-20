"""Sub-step 2a — the durable-write policy: strict priority and default-deny.

Before this, one boolean gated all eight durable sinks together, so "let the
unattended agent write an episode but nothing else" was inexpressible. This
file pins the policy that makes it expressible, and — more importantly — pins
the guarantees it must NOT break:

- `audit_read_only` and the dry-run brake are ABSOLUTE. No allowlist may pierce
  them; `:audit` keeps its zero-delta guarantee.
- The allowlist is DEFAULT-DENY. An unknown sink, or a caller that names no
  sink at all, is refused rather than waved through.

Policy resolution order (`_durable_learning_suppressed`):
    1. audit_read_only            -> deny (absolute)
    2. suppress_durable_learning_writes (dry-run) -> deny (absolute)
    3. durable_writes is None     -> allow   (interactive default)
    4. sink in durable_writes     -> allow
    5. anything else              -> deny    (unknown sink, None sink, absent)

This sub-step changes the MECHANISM only. The unattended profile keeps writing
nothing (`durable_writes=frozenset()`); enabling episode write-back is 2d.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop import AgentLoop
from core.loop_methods2 import KNOWN_DURABLE_SINKS

# Shared cycle/snapshot helpers live with the wiring suite that introduced them.
from tests.test_memory_core_wiring import _drive_one_cycle, _durable_snapshot


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path, durable_writes) -> AgentLoop:
    return build_agent(
        workspace,
        with_memory=True,
        durable_writes=durable_writes,
        approval_provider=None,
    )


# --------------------------------------------------------------------------
# The two absolute brakes — an allowlist must never pierce them.
# --------------------------------------------------------------------------
def test_audit_read_only_is_absolute(tmp_path: Path) -> None:
    agent = _agent(tmp_path, frozenset({"episode"}))
    agent.set_audit_read_only(True)

    for sink in KNOWN_DURABLE_SINKS:
        assert agent._durable_learning_suppressed(sink) is True, (
            f"audit_read_only must deny '{sink}' even when it is allowlisted"
        )


def test_dry_run_brake_is_absolute(tmp_path: Path) -> None:
    agent = _agent(tmp_path, frozenset({"episode"}))
    agent.suppress_durable_learning_writes = True

    for sink in KNOWN_DURABLE_SINKS:
        assert agent._durable_learning_suppressed(sink) is True, (
            f"the dry-run brake must deny '{sink}' even when it is allowlisted"
        )


# --------------------------------------------------------------------------
# Default-deny.
# --------------------------------------------------------------------------
def test_unknown_sink_is_denied(tmp_path: Path) -> None:
    agent = _agent(tmp_path, frozenset({"episode"}))

    assert agent._durable_learning_suppressed("not_a_real_sink") is True
    # ...and a typo of a real one is not quietly treated as that sink.
    assert agent._durable_learning_suppressed("episodes") is True


def test_unnamed_sink_is_denied_under_an_allowlist(tmp_path: Path) -> None:
    agent = _agent(tmp_path, frozenset({"episode"}))

    assert agent._durable_learning_suppressed() is True, (
        "a caller that names no sink must not bypass the allowlist"
    )
    assert agent._durable_learning_suppressed(None) is True


# --------------------------------------------------------------------------
# The three policy shapes.
# --------------------------------------------------------------------------
def test_none_policy_allows_every_KNOWN_sink(tmp_path: Path) -> None:
    """The interactive default: every *known* sink is permitted."""
    agent = _agent(tmp_path, None)

    for sink in KNOWN_DURABLE_SINKS:
        assert agent._durable_learning_suppressed(sink) is False


def test_unknown_sink_is_denied_without_an_allowlist(tmp_path: Path) -> None:
    """`durable_writes=None` must not become an escape hatch for a bad name.

    Sink validity and sink permission are different questions. Answering
    "permitted" before "is this even a real sink" let a typo through on the
    interactive profile — the one with the most stores attached.
    """
    agent = _agent(tmp_path, None)

    assert agent._durable_learning_suppressed("not_a_real_sink") is True
    assert agent._durable_learning_suppressed("episodes") is True


def test_unnamed_sink_is_denied_without_an_allowlist(tmp_path: Path) -> None:
    agent = _agent(tmp_path, None)

    assert agent._durable_learning_suppressed() is True
    assert agent._durable_learning_suppressed(None) is True


def test_empty_allowlist_denies_every_sink(tmp_path: Path) -> None:
    """The unattended profile as of step 1 — write nothing at all."""
    agent = _agent(tmp_path, frozenset())

    for sink in KNOWN_DURABLE_SINKS:
        assert agent._durable_learning_suppressed(sink) is True


def test_allowlist_permits_only_the_named_sink(tmp_path: Path) -> None:
    agent = _agent(tmp_path, frozenset({"episode"}))

    assert agent._durable_learning_suppressed("episode") is False
    for sink in KNOWN_DURABLE_SINKS - {"episode"}:
        assert agent._durable_learning_suppressed(sink) is True, (
            f"'{sink}' is not allowlisted and must be denied"
        )


# --------------------------------------------------------------------------
# Behavioural proof that the split is real, not just a predicate.
# --------------------------------------------------------------------------
def test_allowlisting_one_sink_writes_only_that_sink(tmp_path: Path) -> None:
    """`profile` is allowlisted; the other seven sinks stay untouched.

    Chosen instead of `episode` so this test cannot be confused with 2d
    (autonomous episode write-back), which is a separate sub-step.
    """
    agent = _agent(tmp_path, frozenset({"profile"}))

    before = _durable_snapshot(tmp_path)
    assert _drive_one_cycle(agent, "how much is two plus two")
    after = _durable_snapshot(tmp_path)

    changed = {k for k in before if before[k] != after[k]}
    assert changed == {str(Path("data") / "user_profile.jsonl")}, (
        f"expected only the profile sink to change, got: {sorted(changed)}"
    )
