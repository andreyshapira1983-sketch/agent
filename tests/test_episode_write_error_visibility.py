"""A bug in our code must not look like a memory outage (MIR-052).

`_record_experience_memory` wraps the whole build-and-write block —
`episode_from_agent_cycle(...)`, admission, three store writes — in one
`except Exception`, logged as `smart_memory_error`. That makes two very
different situations indistinguishable:

* **the environment failed** — the store file is locked, the disk is full, a
  local state file is malformed. Degrading quietly is correct: experience
  memory is best-effort and must never take down the user-facing answer.
* **we called our own factory wrong** — a caller passes an argument the
  signature does not accept, or a required one is missing. That is a
  programming error. Swallowed, it surfaces only as "no episode was written",
  which reads as a *storage* problem and sends the next reader looking in the
  wrong place entirely.

Observed live (registry MIR-052): a `TypeError` out of
`episode_from_agent_cycle` was swallowed, the episode delta was zero, and every
test reported nothing changed — with no failure recorded anywhere.

These tests pin the distinction the registry asks for, in its own words:

  * "a caller passing a bad argument to the episode factory raises rather than
    logging `smart_memory_error`";
  * "a simulated storage failure still degrades quietly".

The second is as important as the first. Making the block strict everywhere
would trade a hidden bug for a crashed answer, which is the worse failure.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from app.bootstrap import build_agent
from core.loop import AgentLoop


@pytest.fixture(autouse=True)
def _offline_routing(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "HF_TOKEN"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("AGENT_ALLOW_MOCK_ROUTING", "1")


def _agent(workspace: Path) -> AgentLoop:
    return build_agent(workspace, with_memory=True, approval_provider=None)


def _bank(agent: AgentLoop) -> None:
    """One ordinary banking call, exactly as the loop makes it."""
    agent._record_experience_memory(
        goal_description="read the file",
        question="сколько строк в core/loop_methods2.py",
        answer="Conclusion: 120 строк.",
        tools_used=["file_read"],
        source_labels=["file:core/loop_methods2.py"],
        verified_chunks=1,
        unverified_chunks=0,
        replan_exhausted=False,
        declared_completion="achieved",
    )


def _events(agent: AgentLoop, kind: str) -> list[dict]:
    """Journal entries of one kind, read off the trace file the logger writes."""
    path = Path(agent.log.path)
    if not path.exists():
        return []
    out = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if event.get("event") == kind:
            out.append(event)
    return out


# ── the defect: our own error is disguised ───────────────────────────────────

def test_a_bad_call_to_the_episode_factory_raises(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    """A TypeError from our own factory is a bug, not a memory outage.

    Simulates the real MIR-052 shape: a caller passing an argument the factory
    does not accept. Today this is swallowed into `smart_memory_error` and the
    cycle continues as if memory were merely unavailable.
    """
    agent = _agent(tmp_path)

    def _signature_mismatch(**kwargs):
        raise TypeError(
            "episode_from_agent_cycle() got an unexpected keyword argument 'usage_eligable'"
        )

    monkeypatch.setattr(
        "core.loop_memory_write.episode_from_agent_cycle", _signature_mismatch
    )

    with pytest.raises(TypeError, match="unexpected keyword argument"):
        _bank(agent)


def test_a_bad_call_is_not_reported_as_a_memory_error(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch):
    """And it must not be filed under the label that sends readers elsewhere."""
    agent = _agent(tmp_path)

    def _signature_mismatch(**kwargs):
        raise TypeError("bad keyword argument")

    monkeypatch.setattr(
        "core.loop_memory_write.episode_from_agent_cycle", _signature_mismatch
    )

    with pytest.raises(TypeError):
        _bank(agent)

    assert not _events(agent, "smart_memory_error"), (
        "a programming error was logged as smart_memory_error, which reads as "
        "a storage problem and hides the real cause"
    )


# ── the half that must NOT change: environment failures stay soft ────────────

def test_a_storage_failure_still_degrades_quietly(tmp_path: Path,
                                                  monkeypatch: pytest.MonkeyPatch):
    """The environment failing is not our bug — the answer must still ship.

    This is the guard against over-correcting: if the fix simply removes the
    `except`, a locked file or a full disk starts crashing the user-facing
    cycle, which is worse than the defect being fixed.
    """
    agent = _agent(tmp_path)

    def _disk_is_full(*args, **kwargs):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(type(agent.episodic_store), "save_once", _disk_is_full)

    _bank(agent)   # must not raise

    assert _events(agent, "smart_memory_error"), (
        "a genuine storage failure should still be recorded as smart_memory_error"
    )
