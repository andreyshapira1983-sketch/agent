"""
Supervisor: only component that may create agents (ARCHITECTURE_PLAN_FULL.md).
Поддержка «семейки»: parent_id, role/gender, name, generation (дети, бабушка, дедушка).
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

MAX_AGENTS = 5
SPAWN_DEPTH_LIMIT = 2
AGENT_TTL_SECONDS = 300

_ROOT = Path(__file__).resolve().parent.parent.parent
_STATE_FILE = _ROOT / "data" / "agent_family" / "supervisor_state.json"
_LOCK_FILE = _ROOT / "data" / "agent_family" / "supervisor_state.lock"
_LOCK_TIMEOUT_SEC = 5.0
_LOCK_POLL_SEC = 0.05

_spawn_requests: list[dict[str, Any]] = []
_agents: list[dict[str, Any]] = []  # { "id", "parent_id", "role", "name", "generation", "created_at", "task_spec", ... }


def _load_persisted_state() -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    if not _STATE_FILE.exists():
        return [], []
    try:
        payload = json.loads(_STATE_FILE.read_text(encoding="utf-8"))
    except Exception:
        return [], []
    spawn_requests = payload.get("spawn_requests")
    agents = payload.get("agents")
    return (
        list(spawn_requests) if isinstance(spawn_requests, list) else [],
        list(agents) if isinstance(agents, list) else [],
    )


def _acquire_state_lock(timeout_sec: float = _LOCK_TIMEOUT_SEC) -> int:
    _LOCK_FILE.parent.mkdir(parents=True, exist_ok=True)
    deadline = time.monotonic() + timeout_sec
    while True:
        try:
            return os.open(str(_LOCK_FILE), os.O_CREAT | os.O_EXCL | os.O_RDWR)
        except FileExistsError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Timeout while waiting for supervisor state lock")
            time.sleep(_LOCK_POLL_SEC)


def _release_state_lock(fd: int) -> None:
    try:
        os.close(fd)
    finally:
        try:
            _LOCK_FILE.unlink()
        except OSError:
            pass


def _save_state_unlocked() -> None:
    _STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "spawn_requests": _spawn_requests,
        "agents": _agents,
    }
    tmp_file = _STATE_FILE.with_suffix(".json.tmp")
    tmp_file.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp_file.replace(_STATE_FILE)


def _with_locked_state(mutator):
    global _spawn_requests, _agents
    fd = _acquire_state_lock()
    try:
        _spawn_requests, _agents = _load_persisted_state()
        result = mutator()
        _save_state_unlocked()
        return result
    finally:
        _release_state_lock(fd)


def _refresh_from_disk() -> None:
    global _spawn_requests, _agents
    _spawn_requests, _agents = _load_persisted_state()


def _save_state() -> None:
    fd = _acquire_state_lock()
    try:
        _save_state_unlocked()
    finally:
        _release_state_lock(fd)


def request_spawn(
    task_spec: dict[str, Any] | str,
    depth: int = 0,
    parent_id: str | None = None,
    role: str = "",
    name: str = "",
) -> str | None:
    """
    Request creation of an agent. Optional: parent_id (для семейки), role/gender, name.
    Returns request_id if enqueued, None if rejected.
    """
    def _mutate() -> str | None:
        if depth > SPAWN_DEPTH_LIMIT:
            return None
        if len(_agents) + len([r for r in _spawn_requests if r.get("status") == "pending"]) >= MAX_AGENTS:
            return None
        req_id = f"spawn_{int(time.time() * 1000)}_{len(_spawn_requests)}"
        spec = task_spec if isinstance(task_spec, dict) else {"description": str(task_spec)}
        _spawn_requests.append({
            "id": req_id,
            "task_spec": spec,
            "depth": depth,
            "parent_id": parent_id or None,
            "role": (role or "").strip() or None,
            "name": (name or "").strip() or None,
            "status": "pending",
            "created_at": time.time(),
        })
        return req_id

    return _with_locked_state(_mutate)


def get_pending_spawn_requests() -> list[dict[str, Any]]:
    """For Supervisor: list pending spawn requests to process."""
    _refresh_from_disk()
    return [r for r in _spawn_requests if r.get("status") == "pending"]


def mark_spawn_done(
    request_id: str,
    agent_id: str | None = None,
    parent_id: str | None = None,
    role: str | None = None,
    name: str | None = None,
    generation: int = 0,
) -> None:
    """Mark request as processed; optionally register created agent (with family fields)."""
    def _mutate() -> None:
        for r in _spawn_requests:
            if r.get("id") == request_id:
                r["status"] = "done"
                r["agent_id"] = agent_id
                break
        if agent_id:
            _agents.append({
                "id": agent_id,
                "created_at": time.time(),
                "request_id": request_id,
                "parent_id": parent_id,
                "role": role,
                "name": name or agent_id,
                "generation": generation,
            })

    _with_locked_state(_mutate)


def mark_spawn_rejected(request_id: str, reason: str = "") -> None:
    """Mark request as rejected."""
    def _mutate() -> None:
        for r in _spawn_requests:
            if r.get("id") == request_id:
                r["status"] = "rejected"
                r["reason"] = reason
                break

    _with_locked_state(_mutate)


def expire_agents_by_ttl() -> int:
    """Remove agents that exceeded AGENT_TTL_SECONDS. Returns count expired."""
    def _mutate() -> int:
        global _agents
        now = time.time()
        before = len(_agents)
        _agents = [a for a in _agents if (now - a.get("created_at", 0)) <= AGENT_TTL_SECONDS]
        return before - len(_agents)

    return _with_locked_state(_mutate)


def current_agent_count() -> int:
    _refresh_from_disk()
    return len(_agents)


def list_agents() -> list[dict[str, Any]]:
    """Snapshot of registered agents from persisted supervisor state."""
    _refresh_from_disk()
    return [dict(agent) for agent in _agents]


def clear_supervisor_state() -> None:
    """Clear persisted family/supervisor state."""
    def _mutate() -> None:
        global _spawn_requests, _agents
        _spawn_requests = []
        _agents = []

    _with_locked_state(_mutate)


def forget_in_memory_state() -> None:
    """Drop only current process memory; next read reloads state from disk."""
    global _spawn_requests, _agents
    _spawn_requests = []
    _agents = []


def spawn_depth_limit() -> int:
    return SPAWN_DEPTH_LIMIT


def process_one_spawn_request(start_worker: bool = True, request_id: str | None = None) -> str | None:
    """
    Обработать одну заявку на создание агента: создать запись, наследовать эмоции, запустить воркер.
    Если request_id задан — обработать эту заявку; иначе первую pending. Возвращает agent_id или None.
    """
    spawn_info: dict[str, Any] | None = None

    def _mutate() -> dict[str, Any] | None:
        pending = [r for r in _spawn_requests if r.get("status") == "pending"]
        if not pending:
            return None
        req = next((r for r in pending if r.get("id") == request_id), pending[0]) if request_id else pending[0]
        req_id = req.get("id", "")
        parent_id = req.get("parent_id")
        role = req.get("role") or ""
        name = (req.get("name") or "").strip() or f"agent_{req_id}"
        depth = req.get("depth", 0)
        generation = depth
        if parent_id:
            parent = next((a for a in _agents if a.get("id") == parent_id), None)
            if parent:
                generation = parent.get("generation", 0) + 1
        agent_id = f"child_{int(req.get('created_at', time.time()) * 1000)}_{name[:20].replace(' ', '_')}"
        for r in _spawn_requests:
            if r.get("id") == req_id:
                r["status"] = "done"
                r["agent_id"] = agent_id
                break
        _agents.append({
            "id": agent_id,
            "created_at": time.time(),
            "request_id": req_id,
            "parent_id": parent_id,
            "role": role,
            "name": name,
            "generation": generation,
        })
        return {
            "req_id": req_id,
            "agent_id": agent_id,
            "parent_id": parent_id,
            "role": role,
            "name": name,
            "generation": generation,
        }

    spawn_info = _with_locked_state(_mutate)
    if not spawn_info:
        return None
    req_id = spawn_info["req_id"]
    agent_id = spawn_info["agent_id"]
    parent_id = spawn_info["parent_id"]
    role = spawn_info["role"]
    name = spawn_info["name"]
    generation = spawn_info["generation"]
    if parent_id:
        try:
            from src.personality.emotion_matrix import get_state
            from src.agency.family_store import write_emotion_init
            emotion_state = get_state()
            write_emotion_init(agent_id, emotion_state)
        except Exception:
            pass
    try:
        from src.agency.family_store import update_runtime_state
        update_runtime_state(
            agent_id,
            {
                "status": "spawned",
                "parent_id": parent_id,
                "role": role,
                "name": name,
                "generation": generation,
            },
        )
    except Exception:
        pass
    if start_worker:
        try:
            import subprocess
            import sys
            root = str(__file__).replace("\\", "/").split("src/agency/")[0].rstrip("/")
            env = os.environ.copy()
            env["AGENT_ID"] = agent_id
            env["AGENT_PARENT_ID"] = str(parent_id or "")
            env["AGENT_ROLE"] = str(role)
            env["AGENT_NAME"] = str(name)
            env["AGENT_GENERATION"] = str(generation)
            subprocess.Popen(
                [sys.executable, "-m", "src.agency.worker_agent"],
                cwd=root,
                env=env,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
            )
        except Exception:
            pass
    return agent_id


def get_family_tree(agent_id: str) -> dict[str, Any]:
    """
    Дерево «семейки»: я, мои дети, мои предки (родитель, дед...).
    Если агент не в списке (корневой процесс) — self всё равно возвращается с generation=0.
    """
    _refresh_from_disk()
    try:
        from src.agency.family_store import read_runtime_state, classify_runtime_state
    except Exception:
        read_runtime_state = None
        classify_runtime_state = None

    def _runtime(agent: dict[str, Any] | None) -> dict[str, Any] | None:
        if not read_runtime_state or not agent:
            return None
        runtime = read_runtime_state(agent.get("id", ""))
        if runtime and classify_runtime_state is not None:
            enriched = dict(runtime)
            enriched["effective_status"] = classify_runtime_state(runtime)
            return enriched
        return runtime

    me = next((a for a in _agents if a.get("id") == agent_id), None)
    children = [a for a in _agents if a.get("parent_id") == agent_id]
    ancestors: list[dict[str, Any]] = []
    current = me
    while current and current.get("parent_id"):
        parent = next((a for a in _agents if a.get("id") == current["parent_id"]), None)
        if parent:
            ancestors.append(
                {
                    "id": parent["id"],
                    "name": parent.get("name"),
                    "role": parent.get("role"),
                    "generation": parent.get("generation", 0),
                    "runtime": _runtime(parent),
                }
            )
        current = parent
    self_info = (
        {
            "id": agent_id,
            "name": (me or {}).get("name") or "Agent Host",
            "role": (me or {}).get("role") or "",
            "generation": (me or {}).get("generation", 0),
            "runtime": _runtime(me),
        }
        if me
        else {"id": agent_id, "name": "Agent Host", "role": "", "generation": 0, "runtime": None}
    )
    return {
        "self": self_info,
        "children": [
            {
                "id": a["id"],
                "name": a.get("name"),
                "role": a.get("role"),
                "generation": a.get("generation", 0),
                "runtime": _runtime(a),
            }
            for a in children
        ],
        "ancestors": ancestors,
    }
