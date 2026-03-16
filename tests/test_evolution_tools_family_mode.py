from __future__ import annotations

from src.tools.impl import evolution_tools


def test_create_agent_family_rejects_non_work_role_in_neutral_mode(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_NEUTRAL_FAMILY_MODE", "1")
    out = evolution_tools._create_agent_family("wife", "Alice", "test")
    assert out.startswith("Rejected: role 'wife' is not allowed in neutral mode")


def test_create_agent_family_accepts_work_role_in_neutral_mode(monkeypatch) -> None:
    monkeypatch.setenv("AGENT_NEUTRAL_FAMILY_MODE", "1")

    monkeypatch.setattr(evolution_tools, "process_one_spawn_request", lambda start_worker=True, request_id=None: "child_1", raising=False)

    # Patch imports used inside function via module-level monkeypatching hooks
    class _FakeState:
        @staticmethod
        def get_state():
            return {"agent_id": "root"}

    monkeypatch.setattr("src.state.agent_state.get_state", _FakeState.get_state)

    def _fake_spawn(task_spec, depth=0, parent_id=None, role="", name=""):
        return "req_1"

    monkeypatch.setattr("src.agency.agent_spawner.spawn_agent", _fake_spawn)
    monkeypatch.setattr("src.agency.supervisor.process_one_spawn_request", lambda start_worker=True, request_id=None: "child_1")

    out = evolution_tools._create_agent_family("analyst", "Alice", "test")
    assert "создан" in out.lower() or "created" in out.lower()
