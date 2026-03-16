from __future__ import annotations

from src.agency import family_store


def test_classify_runtime_state_running_to_stale_and_offline(monkeypatch) -> None:
    monkeypatch.setattr(family_store, "RUNTIME_STALE_TTL_SECONDS", 10.0)
    monkeypatch.setattr(family_store, "RUNTIME_OFFLINE_TTL_SECONDS", 20.0)
    runtime = {"status": "running", "heartbeat_at": 100.0, "updated_at": 100.0}

    assert family_store.classify_runtime_state(runtime, now_ts=105.0) == "running"
    assert family_store.classify_runtime_state(runtime, now_ts=111.0) == "stale"
    assert family_store.classify_runtime_state(runtime, now_ts=121.0) == "offline"


def test_classify_runtime_state_completed_not_degraded(monkeypatch) -> None:
    monkeypatch.setattr(family_store, "RUNTIME_STALE_TTL_SECONDS", 10.0)
    monkeypatch.setattr(family_store, "RUNTIME_OFFLINE_TTL_SECONDS", 20.0)
    runtime = {"status": "completed", "heartbeat_at": 100.0, "updated_at": 100.0}

    assert family_store.classify_runtime_state(runtime, now_ts=1000.0) == "completed"