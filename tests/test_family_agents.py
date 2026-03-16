from __future__ import annotations

from pathlib import Path

from src.agency import family_store
from src.agency import supervisor


def _use_temp_supervisor_state(tmp_path, monkeypatch) -> None:
    state_file = tmp_path / "agent_family" / "supervisor_state.json"
    monkeypatch.setattr(supervisor, "_STATE_FILE", state_file)
    monkeypatch.setattr(supervisor, "_LOCK_FILE", tmp_path / "agent_family" / "supervisor_state.lock")

    data_dir = tmp_path / "data"
    monkeypatch.setattr(family_store, "_data_dir", data_dir)
    monkeypatch.setattr(family_store, "_agents_dir", data_dir / "agents")
    monkeypatch.setattr(family_store, "_inbox_dir", data_dir / "agent_inbox")

    def _tmp_agent_dir(agent_id: str) -> Path:
        return (data_dir / "agents") / agent_id.replace("/", "_").replace("\\", "_")

    monkeypatch.setattr(family_store, "_agent_dir", _tmp_agent_dir)
    supervisor.clear_supervisor_state()


def test_process_spawn_request_registers_child_under_root_family(tmp_path, monkeypatch) -> None:
    _use_temp_supervisor_state(tmp_path, monkeypatch)

    req_id = supervisor.request_spawn(
        "Create analyst child",
        depth=1,
        parent_id="root",
        role="analyst",
        name="Alice",
    )

    assert req_id is not None
    child_id = supervisor.process_one_spawn_request(start_worker=False, request_id=req_id)
    tree = supervisor.get_family_tree("root")

    assert child_id is not None
    assert tree["self"]["id"] == "root"
    assert any(child["id"] == child_id and child["name"] == "Alice" for child in tree["children"])


def test_process_spawn_request_sets_generation_from_parent(tmp_path, monkeypatch) -> None:
    _use_temp_supervisor_state(tmp_path, monkeypatch)
    supervisor.mark_spawn_done(
        "req_parent",
        agent_id="parent_1",
        parent_id="root",
        role="mentor",
        name="Parent",
        generation=1,
    )

    req_id = supervisor.request_spawn(
        "Create child",
        depth=1,
        parent_id="parent_1",
        role="researcher",
        name="Child",
    )

    child_id = supervisor.process_one_spawn_request(start_worker=False, request_id=req_id)
    child = next(agent for agent in supervisor.list_agents() if agent["id"] == child_id)

    assert child["generation"] == 2


def test_pending_spawn_requests_survive_memory_reset(tmp_path, monkeypatch) -> None:
    _use_temp_supervisor_state(tmp_path, monkeypatch)

    req_id = supervisor.request_spawn("Create pending child", depth=1, parent_id="root", role="helper", name="Bob")

    supervisor.forget_in_memory_state()
    pending = supervisor.get_pending_spawn_requests()

    assert any(item["id"] == req_id and item["status"] == "pending" for item in pending)


def test_family_tree_survives_memory_reset(tmp_path, monkeypatch) -> None:
    _use_temp_supervisor_state(tmp_path, monkeypatch)

    req_id = supervisor.request_spawn("Create child", depth=1, parent_id="root", role="analyst", name="Eve")
    child_id = supervisor.process_one_spawn_request(start_worker=False, request_id=req_id)

    supervisor.forget_in_memory_state()
    tree = supervisor.get_family_tree("root")

    assert child_id is not None
    assert any(child["id"] == child_id and child["name"] == "Eve" for child in tree["children"])


def test_family_tree_includes_runtime_state(tmp_path, monkeypatch) -> None:
    _use_temp_supervisor_state(tmp_path, monkeypatch)

    req_id = supervisor.request_spawn("Create child", depth=1, parent_id="root", role="analyst", name="Nora")
    child_id = supervisor.process_one_spawn_request(start_worker=False, request_id=req_id)
    assert child_id is not None

    family_store.update_runtime_state(child_id, {"status": "running", "pid": 12345})
    tree = supervisor.get_family_tree("root")
    child = next(item for item in tree["children"] if item["id"] == child_id)

    assert child.get("runtime") is not None
    assert child["runtime"].get("status") == "running"