"""Tests for the autonomous self-build producer wiring in the daemon (TD-026).

These pin the ONE new gate agent_tick owns — a persistent cooldown that runs
*before* build_agent — plus the status/heartbeat surface and the hard safety
invariants: at most one producer call per tick, at most one approval item,
cooldown spent only on ``status="proposed"``, and no self-apply-lane / git
side effects. Everything is driven with fakes (no real LLM/provider/network/git).
"""
from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import agent_tick
from agent_tick import (
    _maybe_produce_self_build,
    _read_producer_state,
)

# ── fakes ─────────────────────────────────────────────────────────────────────


class _FakeInbox:
    def __init__(self) -> None:
        self.added: list[dict] = []

    def add(self, **kwargs):
        self.added.append(kwargs)
        return SimpleNamespace(id="ain-fake")


class _FakeReport:
    """Mirrors ProducerReport.to_dict() for the fields the wrapper reads."""

    def __init__(self, status: str, *, approval_id=None, target_path=None,
                 next_human_action: str = "") -> None:
        self._d = {
            "status": status,
            "approval_id": approval_id,
            "target_path": target_path,
            "next_human_action": next_human_action,
        }

    def to_dict(self) -> dict:
        return dict(self._d)


class _SpyProducer:
    """Records how many times it was called and returns a canned report."""

    def __init__(self, report: _FakeReport) -> None:
        self.report = report
        self.calls = 0

    def __call__(self, **kwargs):
        self.calls += 1
        return self.report


class _SpyBuildAgent:
    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, workspace):
        self.calls += 1
        return SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda role: object())
        )


def _iso(dt: datetime) -> str:
    return dt.isoformat()


# ── the DEFAULT builder — the path no test used to exercise ──────────────────
#
# Every test above hands in `build_agent_fn`, so the branch a real tick takes
# (`build_agent_fn is None`) was never executed by the suite. It had been broken
# since the Phase-7 extraction: the call read
# `__import__("main", fromlist=["build_agent"])`, `main.py` shrank to 47 lines,
# and `build_agent` moved to `app.bootstrap` — so every real tick failed with
# `AttributeError: module 'main' has no attribute 'build_agent'`, reported to the
# operator as the single word "error". Naming the target in a string is what hid
# it: there is no import for a linter or for architecture_invariants to resolve.


def test_no_build_site_names_its_target_in_a_string():
    """The shape of the defect, banned at source level.

    `__import__("main", ...)` cannot be resolved by a linter or by
    `scripts/architecture_invariants.py`, so it survives a refactor that moves
    the function it names. A plain import breaks loudly instead.
    """
    src = Path(agent_tick.__file__).read_text(encoding="utf-8")
    code = "\n".join(
        line for line in src.splitlines() if not line.lstrip().startswith("#")
    )
    assert '__import__("main"' not in code
    assert "__import__('main'" not in code


# ── hard safety: no lane execution / no git side effects ─────────────────────


def test_wrapper_never_imports_or_runs_self_apply_lane_helpers():
    # The wrapper must NOT reference run_self_apply_lane anywhere in its source.
    src = Path(agent_tick.__file__).read_text(encoding="utf-8")
    # Grab just the wrapper function body.
    start = src.index("def _maybe_produce_self_build(")
    end = src.index("# ── main tick", start)
    body = src[start:end]
    assert "run_self_apply_lane" not in body
    # Look for actual method-call syntax, not prose in the docstring.
    for banned in (".commit(", ".push(", ".fetch(", ".pull(", ".merge(",
                   ".reset_hard(", ".stage_all(", ".create_temp_branch("):
        assert banned not in body, banned


def test_real_producer_creates_at_most_one_item_and_no_git(tmp_path: Path):
    # Integration-ish: use the REAL producer with a fake LLM whose Manager
    # selects no target -> no_patch, no item, no git touched.
    from core.self_build_producer import produce_self_apply_proposal

    class _FakeLLM:
        def complete(self, *, system, user, max_tokens=2000, temperature=0.0):
            return json.dumps({"target": None, "diagnosis": "nothing"})

    class _FakeVCS:
        def __init__(self):
            self.mutations = []

        def is_clean(self):
            return True

    from core.approval_inbox import ApprovalInbox

    inbox = ApprovalInbox(path=None)
    vcs = _FakeVCS()

    def _build(ws):
        return SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda role: _FakeLLM())
        )

    out = _maybe_produce_self_build(
        tmp_path, inbox,
        now_iso="2026-07-01T12:00:00+00:00", cooldown_hours=12.0,
        build_agent_fn=_build, producer_fn=produce_self_apply_proposal,
        vcs=vcs, kill_switch=SimpleNamespace(active=False, reason=""),
        budget_snapshot={"windows": []},
    )
    assert out["self_build_status"] == "no_grounded_target"
    assert inbox.list() == []
    assert vcs.mutations == []


# ── outcome journaling (episodic memory) ─────────────────────────────────────


class _SpyEpisodicStore:
    """Captures what the tick journals; can be told to fail on write."""

    def __init__(self, *, boom: bool = False) -> None:
        self.saved: list = []
        self._boom = boom

    def save(self, episode):
        if self._boom:
            raise RuntimeError("disk full")
        self.saved.append(episode)


class _SpyBuildAgentWithMemory:
    """Like _SpyBuildAgent, but the agent it returns carries an episodic store."""

    def __init__(self, store: _SpyEpisodicStore) -> None:
        self.store = store
        self.calls = 0

    def __call__(self, workspace):
        self.calls += 1
        return SimpleNamespace(
            model_router=SimpleNamespace(for_role=lambda role: object()),
            episodic_store=self.store,
        )


def _run_with_memory(tmp_path, report, *, store=None):
    store = store or _SpyEpisodicStore()
    out = _maybe_produce_self_build(
        tmp_path, _FakeInbox(),
        now_iso=_iso(datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)),
        cooldown_hours=12.0,
        build_agent_fn=_SpyBuildAgentWithMemory(store),
        producer_fn=_SpyProducer(report),
    )
    return out, store


def test_critic_veto_is_journaled_with_the_tags_both_readers_query(tmp_path: Path):
    """A veto raised by the daemon has to become a retrievable lesson.

    Two readers select on these tags and each needs a different subset:
    ``recently_vetoed_self_build_targets()`` on ``self-build`` + ``critic_veto``,
    ``recent_self_build_lessons()`` on ``self-build`` + ``failed`` + the target
    path. Miss either set and the next tick retries the same target knowing
    nothing about the veto it already earned.
    """
    out, store = _run_with_memory(
        tmp_path, _FakeReport("critic_veto", target_path="core/redaction.py")
    )
    assert out["self_build_status"] == "critic_veto"
    assert len(store.saved) == 1
    tags = set(store.saved[0].tags)
    assert {"self-build", "critic_veto"} <= tags
    assert {"self-build", "failed", "core/redaction.py"} <= tags


def test_journaling_failure_never_breaks_the_tick(tmp_path: Path):
    """Memory is best-effort here: a dead store must not cost us the outcome."""
    out, store = _run_with_memory(
        tmp_path,
        _FakeReport("proposed", approval_id="ain-1"),
        store=_SpyEpisodicStore(boom=True),
    )
    assert out["self_build_status"] == "proposed"
    assert store.saved == []
    assert _read_producer_state(tmp_path)["last_proposed_at"]  # cooldown still spent
