

from __future__ import annotations

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

app = FastAPI(title="Agent Dashboard API")
app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

"""
Dashboard API server: state, queue, audit, workspace.
Запуск в отдельном потоке рядом с Telegram ботом (main не блокируется).
Endpoints: GET /api/dashboard (всё для карты), GET /api/state, /api/queue, /api/audit, /api/workspace.
"""

@app.get("/health")
def health():
    return {"status": "ok"}

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

_log = logging.getLogger(__name__)

_DASHBOARD_PORT = int(os.getenv("DASHBOARD_PORT", "8765"))
_ROOT = Path(__file__).resolve().parent.parent.parent

_SKIP_NAMES = {".git", ".mypy_cache", ".pytest_cache", ".ruff_cache", "__pycache__", "node_modules", "venv", ".venv"}


def _build_file_tree(
    path: Path, max_depth: int = 4, limit: int = 500, depth: int = 0
) -> list[dict[str, Any]]:
    """Дерево папок и файлов проекта. Узел: name, path, isDir, children (если папка)."""
    if depth >= max_depth or limit <= 0:
        return []
    result: list[dict[str, Any]] = []
    try:
        children = sorted(path.iterdir(), key=lambda p: (not p.is_dir(), p.name.lower()))
    except OSError:
        return []
    for entry in children:
        if entry.name in _SKIP_NAMES or entry.name.startswith("."):
            continue
        if limit <= 0:
            break
        limit -= 1
        try:
            rel = entry.relative_to(_ROOT)
        except ValueError:
            rel = Path(entry.name)
        node: dict[str, Any] = {
            "name": entry.name,
            "path": str(rel).replace("\\", "/"),
            "isDir": entry.is_dir(),
        }
        if entry.is_dir():
            node["children"] = _build_file_tree(entry, max_depth, limit, depth + 1)
        result.append(node)
    return result


def _safe(fn, default: Any = None, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except Exception as e:
        _log.debug("Dashboard API %s: %s", fn.__name__, e)
        return default


def _get_live_state() -> dict[str, Any]:
    """Сводка «живого» состояния: время суток, эмоции, последняя цель планировщика."""
    from datetime import datetime
    live: dict[str, Any] = {}
    now = datetime.now()
    h = now.hour
    if 5 <= h < 12:
        tod = "утро"
    elif 12 <= h < 18:
        tod = "день"
    elif 18 <= h < 23:
        tod = "вечер"
    else:
        tod = "ночь"
    live["time_of_day"] = tod
    live["local_time"] = now.strftime("%Y-%m-%d %H:%M:%S")
    try:
        from src.personality.emotion_matrix import get_state, get_dominant, get_intensity, get_history
        state = get_state()
        name, value = get_dominant()
        intensity = get_intensity(value)
        live["emotion"] = {
            "dominant": name,
            "value": round(float(value), 2),
            "intensity": intensity,
            "state": {k: round(float(v), 2) for k, v in (state or {}).items()},
            "history": get_history(30),
        }
    except Exception as e:
        live["emotion_error"] = str(e)
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(80)
        last_goal = ""
        last_goal_ts = ""
        last_act = ""
        for e in tail:
            if e.get("action") == "autonomous_cycle_end":
                d = e.get("details") or {}
                last_goal = str(d.get("goal") or "")
                last_goal_ts = (e.get("ts") or "")[:19]
        for e in tail[::-1]:
            if e.get("action") == "autonomous_act":
                d = e.get("details") or {}
                last_act = f"{d.get('tool', '')} (success={d.get('success', '')})"
                break
        live["planner"] = {
            "last_goal": last_goal,
            "last_goal_ts": last_goal_ts,
            "last_action": last_act,
        }
    except Exception as e:
        live["planner_error"] = str(e)
    try:
        from src.hitl.initiative_visor_state import get_state as get_visor_state
        live["initiative_visor"] = get_visor_state()
    except Exception as e:
        live["initiative_visor_error"] = str(e)
    try:
        from src.tasks.queue import peek, size
        first = peek(1)
        if size() > 0 and first:
            t = first[0]
            live["current_task"] = f"{t.get('tool', '')} — {t.get('arguments_preview', '')}"[:80]
        else:
            live["current_task"] = ""
    except Exception:
        live["current_task"] = ""
    try:
        from src.hitl.audit_log import get_audit_tail
        tail = get_audit_tail(5)
        visor = live.get("initiative_visor") or {}
        live["focus"] = {
            "goal": (live.get("planner") or {}).get("last_goal") or "",
            "current_task": live.get("current_task") or "",
            "thought": (visor.get("reason") or visor.get("meta_goal") or "").strip() or "",
            "last_events": [{"action": e.get("action"), "ts": e.get("ts"), "details": e.get("details")} for e in tail],
        }
    except Exception:
        live["focus"] = {"goal": "", "current_task": "", "thought": "", "last_events": []}
    return live


def get_dashboard_data() -> dict[str, Any]:
    """Один объект для фронта: state, queue, audit, metrics, workspace, live_state."""
    out: dict[str, Any] = {
        "ts": None,
        "state": {},
        "queue": [],
        "audit": [],
        "metrics": {},
        "workspace": "",
        "modules": [],
        "live_state": {},
        "file_tree": [],
    }

    def _ts():
        from datetime import datetime, timezone
        out["ts"] = datetime.now(timezone.utc).isoformat()

    _ts()
    out["live_state"] = _safe(_get_live_state, {})
    try:
        out["file_tree"] = _build_file_tree(_ROOT)
    except Exception:
        out["file_tree"] = []

    out["state"] = _safe(_get_state_for_api) or {}

    try:
        from src.tasks.queue import peek, size
        out["queue"] = [{"size": size(), "tasks": peek(30)}]
    except Exception:
        out["queue"] = [{"size": 0, "tasks": []}]

    try:
        from src.hitl.audit_log import get_audit_tail
        out["audit"] = get_audit_tail(40)
    except Exception:
        out["audit"] = []

    try:
        from src.monitoring.metrics import get_metrics
        out["metrics"] = get_metrics() or {}
    except Exception:
        pass

    try:
        from src.environment.filesystem import build_tree_snapshot
        out["workspace"] = build_tree_snapshot(_ROOT, max_depth=3, max_entries=150)
    except Exception:
        out["workspace"] = ""

    # Модули для графа: узлы системы
    out["modules"] = [
        {"id": "agent", "label": "Agent", "type": "core"},
        {"id": "orchestrator", "label": "Orchestrator", "type": "core"},
        {"id": "queue", "label": f"Queue ({out['queue'][0].get('size', 0)})", "type": "queue"},
        {"id": "policy", "label": "Policy", "type": "governance"},
        {"id": "tools", "label": "Tools", "type": "tools"},
        {"id": "planning", "label": "Planning", "type": "planning"},
        {"id": "communication", "label": "Telegram", "type": "comm"},
        {"id": "audit", "label": "Audit", "type": "hitl"},
    ]
    out["edges"] = [
        {"from": "agent", "to": "orchestrator"},
        {"from": "orchestrator", "to": "queue"},
        {"from": "orchestrator", "to": "policy"},
        {"from": "orchestrator", "to": "tools"},
        {"from": "orchestrator", "to": "planning"},
        {"from": "agent", "to": "communication"},
        {"from": "orchestrator", "to": "audit"},
    ]
    return out


def _get_state_for_api() -> dict[str, Any]:
    from src.hitl.dashboard_api import get_state_for_dashboard
    return get_state_for_dashboard() or {}


def run_dashboard_server(host: str = "127.0.0.1", port: int | None = None) -> None:
    """Запуск HTTP-сервера дашборда в текущем потоке (блокирует)."""
    port = port or _DASHBOARD_PORT
    try:
        from fastapi import FastAPI
        from fastapi.middleware.cors import CORSMiddleware
        from fastapi.responses import HTMLResponse, JSONResponse
        from fastapi.staticfiles import StaticFiles
    except ImportError as e:
        _log.warning("Dashboard server skipped (install fastapi/uvicorn): %s", e)
        return

    app = FastAPI(title="Agent Dashboard API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.get("/api/dashboard")
    def api_dashboard():
        return JSONResponse(get_dashboard_data())

    @app.get("/api/state")
    def api_state():
        return JSONResponse(_safe(_get_state_for_api) or {})

    @app.get("/api/queue")
    def api_queue():
        try:
            from src.tasks.queue import peek, size
            return JSONResponse({"size": size(), "tasks": peek(50)})
        except Exception:
            return JSONResponse({"size": 0, "tasks": []})

    @app.get("/api/audit")
    def api_audit(n: int = 50):
        try:
            from src.hitl.audit_log import get_audit_tail
            return JSONResponse(get_audit_tail(n))
        except Exception:
            return JSONResponse([])

    @app.get("/api/workspace")
    def api_workspace(depth: int = 3, entries: int = 200):
        try:
            from src.environment.filesystem import build_tree_snapshot
            return JSONResponse({"tree": build_tree_snapshot(_ROOT, max_depth=depth, max_entries=entries)})
        except Exception as e:
            return JSONResponse({"tree": "", "error": str(e)})

    dashboard_dir = _ROOT / "dashboard"
    _dashboard_fallback = """<!DOCTYPE html><html><head><meta charset="utf-8"><title>Agent Dashboard</title></head><body>
    <h1>Agent Dashboard</h1>
    <p>API: <a href="/api/dashboard">/api/dashboard</a>, <a href="/api/state">/api/state</a>, <a href="/api/queue">/api/queue</a>, <a href="/api/audit">/api/audit</a>, <a href="/api/workspace">/api/workspace</a>.</p>
    <p><a href="/health">/health</a></p>
    </body></html>"""

    @app.get("/dashboard")
    def serve_dashboard():
        return HTMLResponse(_dashboard_fallback)

    @app.get("/dashboard/")
    def serve_dashboard_slash():
        return HTMLResponse(_dashboard_fallback)

    if dashboard_dir.is_dir():
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")

    import uvicorn
    uvicorn.run(app, host=host, port=port, log_level="warning")


def start_dashboard_thread(host: str = "127.0.0.1", port: int | None = None) -> threading.Thread | None:
    """Запустить дашборд в фоновом потоке (для работы вместе с Telegram ботом)."""
    port = port or _DASHBOARD_PORT
    def _run():
        run_dashboard_server(host=host, port=port)
    t = threading.Thread(target=_run, daemon=True, name="dashboard-server")
    t.start()
    _log.info("Dashboard server thread started: http://%s:%s", host, port)
    return t

    # Экспорт FastAPI app для ASGI (uvicorn)
    from fastapi import FastAPI
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import HTMLResponse, JSONResponse
    from fastapi.staticfiles import StaticFiles

    app = FastAPI(title="Agent Dashboard API")
    app.add_middleware(CORSMiddleware, allow_origins=["*"], allow_methods=["GET"], allow_headers=["*"])

    @app.get("/api/dashboard")
    def api_dashboard():
        return JSONResponse(get_dashboard_data())

    @app.get("/api/state")
    def api_state():
        return JSONResponse(_safe(_get_state_for_api) or {})

    @app.get("/api/queue")
    def api_queue():
        try:
            from src.tasks.queue import peek, size
            return JSONResponse({"size": size(), "tasks": peek(50)})
        except Exception:
            return JSONResponse({"size": 0, "tasks": []})

    @app.get("/api/audit")
    def api_audit(n: int = 50):
        try:
            from src.hitl.audit_log import get_audit_tail
            return JSONResponse(get_audit_tail(n))
        except Exception:
            return JSONResponse([])

    @app.get("/api/workspace")
    def api_workspace(depth: int = 3, entries: int = 200):
        try:
            from src.environment.filesystem import build_tree_snapshot
            return JSONResponse({"tree": build_tree_snapshot(_ROOT, max_depth=depth, max_entries=entries)})
        except Exception as e:
            return JSONResponse({"tree": "", "error": str(e)})

    dashboard_dir = _ROOT / "dashboard"
    if dashboard_dir.is_dir():
        app.mount("/dashboard", StaticFiles(directory=str(dashboard_dir), html=True), name="dashboard")
    else:
        @app.get("/dashboard")
        def serve_dashboard():
            return HTMLResponse("<p>Dashboard static files not found. Create <code>dashboard/</code> with index.html.</p>")
