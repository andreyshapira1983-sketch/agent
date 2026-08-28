"""Autonomous self-build proposal step (auto-propose, human-gated apply).

The runtime, after its health pass, proposes at most one low-risk split into the
approval inbox WITHOUT applying it — so the agent surfaces improvement work on its
own tick while a human still approves before anything is applied.
"""
from __future__ import annotations

import core.autonomous_runtime_proposals as prop_mod
from core.approval_inbox import ApprovalInbox
from core.autonomous_runtime import AutonomousRuntime, AutonomousRuntimeConfig


class _FakeStore:
    def __init__(self) -> None:
        self.saved: list = []

    def save(self, episode) -> None:
        self.saved.append(episode)


class _FakeAgent:
    def __init__(self, store=None, *, has_llm=True) -> None:
        self.episodic_store = store
        self.model_router = None  # forces fallback to .llm
        self.llm = object() if has_llm else None
        self.log = None


class _FakeReport:
    def __init__(self, data: dict) -> None:
        self._data = data

    def to_dict(self) -> dict:
        return self._data


def _runtime(agent, workspace, inbox=None) -> AutonomousRuntime:
    return AutonomousRuntime(
        agent, workspace=workspace, approval_inbox=inbox or ApprovalInbox()
    )


def _real_run_config() -> AutonomousRuntimeConfig:
    # dry_run=False so the proposal step is not skipped.
    return AutonomousRuntimeConfig(dry_run=False, effects_approved=True)


def test_proposal_records_episode_on_success(monkeypatch, tmp_path) -> None:
    calls = {}

    def _fake_produce(**kwargs):
        calls.update(kwargs)
        return _FakeReport({
            "status": "proposed",
            "target_path": "core/campaign.py",
            "approval_id": "ain_x",
            "reason": "split it",
            "veto_reasons": [],
        })

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    store = _FakeStore()
    rt = _runtime(_FakeAgent(store), tmp_path)

    result = rt._run_self_build_proposal(_real_run_config())

    assert result is not None and result["status"] == "proposed"
    # The producer was actually invoked with our inbox + workspace.
    assert calls["workspace"] == tmp_path
    # The outcome was journalled as a lesson.
    assert len(store.saved) == 1
    assert store.saved[0].outcome == "success"


def test_proposal_records_veto_reason(monkeypatch, tmp_path) -> None:
    def _fake_produce(**kwargs):
        return _FakeReport({
            "status": "critic_veto",
            "target_path": "core/planner.py",
            "reason": "empty generated content",
            "veto_reasons": ["empty generated content"],
        })

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    store = _FakeStore()
    rt = _runtime(_FakeAgent(store), tmp_path)

    result = rt._run_self_build_proposal(_real_run_config())

    assert result["status"] == "critic_veto"
    assert store.saved[0].outcome == "failed"
    assert "empty generated content" in store.saved[0].summary


def test_a_decline_leaves_its_reason_in_the_trace(monkeypatch, tmp_path) -> None:
    """MIR-185, заказ второго экзаменатора: «полный сырой журнал одного
    отказавшего тика … включая reason». Красный свидетель: до починки событие
    `self_build_proposal` несло status/target/approval_id/veto_reasons и
    ТЕРЯЛО причину — отказ 08:31Z 2026-08-28 пришлось диагностировать живым
    зондом спустя 3,5 часа по уже изменившемуся состоянию. Улика обязана
    рождаться в момент отказа."""
    class _Log:
        def __init__(self) -> None:
            self.events: list[tuple[str, dict]] = []

        def log(self, event, payload) -> None:
            self.events.append((event, payload))

    def _fake_produce(**kwargs):
        return _FakeReport({
            "status": "no_grounded_target",
            "target_path": None,
            "approval_id": None,
            "reason": "grounded target 'core/self_build_producer.py' is critical",
            "veto_reasons": [],
        })

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    agent = _FakeAgent(_FakeStore())
    agent.log = _Log()
    rt = _runtime(agent, tmp_path)

    rt._run_self_build_proposal(_real_run_config())

    traced = [p for e, p in agent.log.events if e == "self_build_proposal"]
    assert traced, "событие self_build_proposal не записано вовсе"
    assert traced[0].get("reason") == (
        "grounded target 'core/self_build_producer.py' is critical"
    ), f"причина отказа потеряна по дороге в трассу: {traced[0]}"


def test_proposal_skipped_in_dry_run(monkeypatch, tmp_path) -> None:
    called = {"n": 0}

    def _fake_produce(**kwargs):
        called["n"] += 1
        return _FakeReport({"status": "proposed"})

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    rt = _runtime(_FakeAgent(_FakeStore()), tmp_path)

    # Default config is dry_run=True.
    assert rt._run_self_build_proposal(AutonomousRuntimeConfig()) is None
    assert called["n"] == 0


def test_proposal_skipped_when_pending_exists(monkeypatch, tmp_path) -> None:
    called = {"n": 0}

    def _fake_produce(**kwargs):
        called["n"] += 1
        return _FakeReport({"status": "proposed"})

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    inbox = ApprovalInbox()
    inbox.add(
        operation="self_apply_lane.run",
        summary="existing pending split",
        risk="reversible",
    )
    rt = _runtime(_FakeAgent(_FakeStore()), tmp_path, inbox=inbox)

    assert rt._run_self_build_proposal(_real_run_config()) is None
    assert called["n"] == 0  # expensive producer not run while one is pending


def test_proposal_skipped_without_llm(monkeypatch, tmp_path) -> None:
    called = {"n": 0}

    def _fake_produce(**kwargs):
        called["n"] += 1
        return _FakeReport({"status": "proposed"})

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _fake_produce)
    rt = _runtime(_FakeAgent(_FakeStore(), has_llm=False), tmp_path)

    assert rt._run_self_build_proposal(_real_run_config()) is None
    assert called["n"] == 0


def test_proposal_swallows_producer_errors(monkeypatch, tmp_path) -> None:
    def _boom(**kwargs):
        raise RuntimeError("producer exploded")

    monkeypatch.setattr(prop_mod, "produce_self_apply_proposal", _boom)
    rt = _runtime(_FakeAgent(_FakeStore()), tmp_path)

    # Must never raise back into the run path.
    assert rt._run_self_build_proposal(_real_run_config()) is None


def test_config_flag_defaults_on() -> None:
    assert AutonomousRuntimeConfig().enable_self_build is True
