"""
Хранение данных семейки: наследование эмоций (emotion_init), inbox для коммуникации между агентами.
Пути: data/agents/{agent_id}/emotion_init.json, data/agent_inbox/{agent_id}.json
"""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

_root = Path(__file__).resolve().parent.parent.parent
_data_dir = _root / "data"
_agents_dir = _data_dir / "agents"
_inbox_dir = _data_dir / "agent_inbox"
RUNTIME_STALE_TTL_SECONDS = float(os.environ.get("AGENT_RUNTIME_STALE_TTL_SECONDS", "60"))
RUNTIME_OFFLINE_TTL_SECONDS = float(os.environ.get("AGENT_RUNTIME_OFFLINE_TTL_SECONDS", "300"))


def _agent_dir(agent_id: str) -> Path:
    return _agents_dir / agent_id.replace("/", "_").replace("\\", "_")


def write_emotion_init(agent_id: str, emotion_state: dict[str, float]) -> None:
    """Сохранить начальное состояние эмоций (для наследования дочерним агентом)."""
    d = _agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    (d / "emotion_init.json").write_text(json.dumps(emotion_state, ensure_ascii=False), encoding="utf-8")


def read_emotion_init(agent_id: str) -> dict[str, float] | None:
    """Прочитать начальное состояние эмоций (дочерний агент при старте)."""
    path = _agent_dir(agent_id) / "emotion_init.json"
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def append_inbox(agent_id: str, from_agent_id: str, message: str) -> None:
    """Добавить сообщение в inbox агента (коммуникация внутри семейки)."""
    _inbox_dir.mkdir(parents=True, exist_ok=True)
    path = _inbox_dir / f"{agent_id.replace('/', '_')}.json"
    import time
    entry = {"from": from_agent_id, "message": message[:2000], "ts": time.time()}
    if path.exists():
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            data = []
    else:
        data = []
    data.append(entry)
    path.write_text(json.dumps(data, ensure_ascii=False, indent=0), encoding="utf-8")


def read_inbox(agent_id: str, clear_after: bool = True) -> list[dict[str, Any]]:
    """Прочитать и опционально очистить inbox агента."""
    path = _inbox_dir / f"{agent_id.replace('/', '_')}.json"
    if not path.exists():
        return []
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return []
    if clear_after:
        try:
            path.unlink()
        except Exception:
            pass
    return data if isinstance(data, list) else []


def write_runtime_state(agent_id: str, state: dict[str, Any]) -> None:
    """Записать runtime-state дочернего агента (status, timestamps, summary)."""
    d = _agent_dir(agent_id)
    d.mkdir(parents=True, exist_ok=True)
    payload = dict(state or {})
    payload.setdefault("updated_at", time.time())
    (d / "runtime_state.json").write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def read_runtime_state(agent_id: str) -> dict[str, Any] | None:
    """Прочитать runtime-state дочернего агента."""
    path = _agent_dir(agent_id) / "runtime_state.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else None
    except Exception:
        return None


def update_runtime_state(agent_id: str, patch: dict[str, Any]) -> None:
    """Обновить runtime-state частично (merge patch)."""
    current = read_runtime_state(agent_id) or {}
    merged = dict(current)
    merged.update(patch or {})
    merged["updated_at"] = time.time()
    write_runtime_state(agent_id, merged)


def classify_runtime_state(runtime: dict[str, Any] | None, now_ts: float | None = None) -> str:
    """Classify runtime status with TTL rules: running/spawned may become stale/offline."""
    if not runtime:
        return "unknown"
    now = now_ts if now_ts is not None else time.time()
    status = str(runtime.get("status") or "unknown").strip().lower()
    updated_at = runtime.get("updated_at")
    heartbeat_at = runtime.get("heartbeat_at")
    last_seen = heartbeat_at if isinstance(heartbeat_at, (int, float)) else updated_at
    if not isinstance(last_seen, (int, float)):
        return status or "unknown"
    age = max(0.0, now - float(last_seen))
    if status in ("running", "spawned"):
        if age >= RUNTIME_OFFLINE_TTL_SECONDS:
            return "offline"
        if age >= RUNTIME_STALE_TTL_SECONDS:
            return "stale"
    return status or "unknown"
