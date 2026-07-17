"""Concurrency contract for the FastAPI server's shared agent singleton.

The server exposes ONE shared AgentLoop (with cross-request session memory).
FastAPI runs sync endpoints in a thread pool, so the server must:

  1. build that agent at most once even under a concurrent first burst
     (no leaked second instance from a construction race);
  2. serialize agent.run() so two concurrent /ask calls never interleave the
     shared agent's TraceLogger / WorkingMemory / usage-ledger writes.
"""
from __future__ import annotations

import importlib
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest


@pytest.fixture()
def server(monkeypatch: pytest.MonkeyPatch, tmp_path: Path):
    # The module reads AGENT_API_TOKEN at import and refuses to load without it.
    monkeypatch.setenv("AGENT_API_TOKEN", "test-token-123")
    monkeypatch.setenv("AGENT_WORKSPACE", str(tmp_path))
    # api.server calls load_dotenv() at import. Stub it so reloading the module
    # here never loads the developer's real .env into the pytest process
    # environment — that would leak secrets into the run and pollute
    # env-sensitive tests (e.g. the model router's route-reason assertions).
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import api.server as server_mod
    server_mod = importlib.reload(server_mod)
    return server_mod


def test_concurrent_ask_calls_do_not_interleave_agent_run(server, monkeypatch):
    """Two concurrent /ask calls must be serialized on the shared agent."""
    max_concurrent = 0
    current = 0
    lock = threading.Lock()

    class _RecordingAgent:
        def __init__(self) -> None:
            self.log = type("L", (), {"trace_id": "trace-x"})()
            self.llm = type("M", (), {"usage_summary": staticmethod(lambda: {})})()

        def run(self, *, user_question, file_hint=None):
            nonlocal max_concurrent, current
            with lock:
                current += 1
                max_concurrent = max(max_concurrent, current)
            # Hold the "critical section" long enough that an unserialized
            # implementation would overlap here.
            time.sleep(0.02)
            with lock:
                current -= 1
            return f"answer:{user_question}"

    # Pre-seed the singleton so _get_agent() returns our recording double.
    server._agent = _RecordingAgent()

    def call(i: int):
        return server.ask(server.AskRequest(question=f"q{i}"))

    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(call, range(8)))

    assert len(results) == 8
    assert all(r.answer.startswith("answer:q") for r in results)
    # The run lock must have prevented any overlap inside agent.run().
    assert max_concurrent == 1


def test_get_agent_builds_only_once_under_concurrent_first_burst(server, monkeypatch):
    """A concurrent first burst must build the agent exactly once."""
    build_count = 0
    build_lock = threading.Lock()

    def slow_builder():
        nonlocal build_count
        with build_lock:
            build_count += 1
        time.sleep(0.02)  # widen the race window
        return object()

    monkeypatch.setattr(server, "_build_server_agent", slow_builder)
    server._agent = None  # force lazy construction

    with ThreadPoolExecutor(max_workers=8) as pool:
        agents = list(pool.map(lambda _: server._get_agent(), range(8)))

    # Built once, and every caller got the same instance.
    assert build_count == 1
    assert all(a is agents[0] for a in agents)
