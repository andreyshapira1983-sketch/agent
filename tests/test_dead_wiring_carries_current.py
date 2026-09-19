"""Dead wiring carries current (audit W1–W5, block 7, 2026-09-03).

- W1 `data/incidents.jsonl` was write-only: `needing_human`/`summary` had no
  production caller, and the dedup on an open incident silenced every later
  stop because nothing ever resolved the first one.
- W2 `tools/journal_append.py` stored `workspace_root` and never read it; the
  boundary was a string prefix, `data/../../x.jsonl` passed, and the file
  landed relative to the current directory.
- W3 `web_fetches` had limits, a kill-switch and a health line reading
  `0/300` — and no site that ever charged it.
- W4 the memory door asked the write policy without the recent-writes log:
  the echo antibody never saw door writes and `memory_writes.jsonl` was
  never fed by them.
- W5 `AGENT_FETCH_ALLOW_HOSTS`/`DENY_HOSTS` were read by the tools and
  documented nowhere.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from core.budget_ledger import BudgetLedger, BudgetWindow
from core.incident import IncidentLog
from core.memory_policy import MemoryWritePolicy
from core.persistent_memory import PersistentMemoryStore, memory_door_verdict
from tools.journal_append import JournalAppendTool
from tools.web_fetch import WebFetchTool
from tools.web_search import WebSearchTool

# ── W1 ───────────────────────────────────────────────────────────────────────


def _runtime_with_incidents(tmp_path: Path):
    from core.autonomous_runtime import AutonomousRuntime

    rt = AutonomousRuntime.__new__(AutonomousRuntime)
    rt.incident_log = IncidentLog(path=tmp_path / "data" / "incidents.jsonl")
    rt.events: list[tuple[str, dict]] = []
    rt._log = lambda event, payload=None: rt.events.append((event, dict(payload or {})))
    return rt


def test_a_repeated_stop_grows_the_open_incident(tmp_path: Path) -> None:
    rt = _runtime_with_incidents(tmp_path)

    rt._record_incident("circuit open: 3 failures", [])
    rt._record_incident("circuit open: 5 failures", [])

    opened = rt.incident_log.open_incidents()
    assert len(opened) == 1, "a second incident for the same trigger would be spam"
    assert "recurred" in opened[0].postmortem_note and "5 failures" in opened[0].postmortem_note
    assert [e for e, _ in rt.events] == ["incident_opened", "incident_recurred"]


def test_open_incidents_reach_the_status_line(tmp_path: Path) -> None:
    log = IncidentLog(path=tmp_path / "data" / "incidents.jsonl")
    assert log.status_line() == ""

    log.open_incident(severity="high", trigger="autonomous_run_stopped",
                      affected_module="core.autonomous_runtime")

    line = log.status_line()
    assert "1 open" in line and "1 awaiting a human" in line and "autonomous_run_stopped" in line


def test_the_daemon_status_prints_the_incident_line() -> None:
    """Wiring: the reader is called where the operator looks."""
    import inspect

    import agent_tick

    src = inspect.getsource(agent_tick._print_status)
    assert "status_line()" in src and "INCIDENT_LOG_PATH" in src


# ── W2 ───────────────────────────────────────────────────────────────────────


def test_journal_append_writes_into_the_workspace_not_the_cwd(tmp_path: Path, monkeypatch) -> None:
    workspace = tmp_path / "ws"
    (workspace / "data").mkdir(parents=True)
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    tool = JournalAppendTool(workspace_root=workspace)

    tool.run(path="data/journal.jsonl", record={"a": 1})

    assert (workspace / "data" / "journal.jsonl").is_file()
    assert not (elsewhere / "data").exists(), "the record landed relative to the CWD"


@pytest.mark.parametrize("path", ["data/../../x.jsonl", "data/../core/loop.jsonl", "data/sub/../../y.jsonl"])
def test_journal_append_refuses_a_path_that_escapes_data(tmp_path: Path, path: str) -> None:
    workspace = tmp_path / "ws"
    (workspace / "data").mkdir(parents=True)
    tool = JournalAppendTool(workspace_root=workspace)

    with pytest.raises(ValueError):
        tool.run(path=path, record={"a": 1})
    assert not (tmp_path / "x.jsonl").exists() and not (tmp_path / "y.jsonl").exists()


def test_journal_append_declares_its_arguments() -> None:
    assert "path" in JournalAppendTool.arguments and "record" in JournalAppendTool.arguments


# ── W3 ───────────────────────────────────────────────────────────────────────


class _Response:
    def __init__(self) -> None:
        self.headers = {"Content-Type": "text/plain; charset=utf-8"}
        self.status = 200

    def read(self, n: int = -1) -> bytes:
        return b"hello"

    def geturl(self) -> str:
        return "https://example.org/"

    def getheader(self, name, default=None):
        return self.headers.get(name, default)

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _Opener:
    def __init__(self) -> None:
        self.calls = 0

    def open(self, req, timeout=None):
        self.calls += 1
        return _Response()


def _ledger(tmp_path: Path, limit: int) -> BudgetLedger:
    return BudgetLedger(
        path=tmp_path / "data" / "budget_ledger.jsonl",
        windows=(BudgetWindow(name="day", seconds=86400, limits={"web_fetches": limit}),),
    )


def test_a_fetch_charges_the_persistent_web_fetches_meter(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit=5)
    opener = _Opener()
    tool = WebFetchTool(opener=opener, budget_ledger=ledger)

    tool.run("https://example.org/")

    snapshot = ledger.snapshot()
    used = json.dumps(snapshot)
    assert '"web_fetches"' in used and opener.calls == 1
    assert any(r.counter == "web_fetches" for r in ledger.load_records()), (
        "the fetch left no row on the ledger — the meter still reads zero"
    )


def test_an_exhausted_web_fetches_window_refuses_before_the_network(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit=1)
    opener = _Opener()
    tool = WebFetchTool(opener=opener, budget_ledger=ledger)
    tool.run("https://example.org/")

    with pytest.raises(PermissionError):
        tool.run("https://example.org/again")
    assert opener.calls == 1, "the refused fetch still went out"


def test_web_search_is_metered_too(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path, limit=1)
    ledger.reserve("web_fetches", reason="spent by a neighbour")  # the window is full
    tool = WebSearchTool(budget_ledger=ledger)

    with pytest.raises(PermissionError):
        tool.run("anything")  # refused before any network call


def test_an_unmetered_tool_still_works() -> None:
    """Control: hand-built tools (tests, scripts) carry no ledger."""
    opener = _Opener()
    out = WebFetchTool(opener=opener).run("https://example.org/")
    assert opener.calls == 1 and out


def test_bootstrap_hands_the_ledger_to_every_egress_tool() -> None:
    import inspect

    from app import bootstrap

    src = inspect.getsource(bootstrap.build_agent)
    assert 'for egress_tool in ("web_search", "web_fetch", "rss_fetch")' in src
    assert ".budget_ledger = budget_ledger" in src


# ── W4 ───────────────────────────────────────────────────────────────────────

_PROSE = (
    "Суд выносит вердикт по журналам, а модель лишь предлагает объяснения; "
    "выбор принадлежит проверке, не автору."
)


def test_a_door_write_feeds_the_recent_writes_log(tmp_path: Path) -> None:
    store = PersistentMemoryStore(tmp_path / "data" / "pm.jsonl")

    mem_id, reason = memory_door_verdict(
        store, MemoryWritePolicy(), _PROSE, "[ВЫВОД, проверен боем]", "test:w4",
    )

    assert mem_id and not reason
    log = tmp_path / "data" / "memory_writes.jsonl"
    assert log.is_file(), "the echo antibody's log was never fed by the door"
    assert json.loads(log.read_text(encoding="utf-8").splitlines()[0])["source"] == "agent-auto"


def test_the_door_consults_the_antibody(tmp_path: Path, monkeypatch) -> None:
    """The policy receives the recent writes — not an empty default."""
    from core import memory_policy

    seen: dict = {}
    original = memory_policy.MemoryWritePolicy.decide

    def _spy(self, content, tags=(), source="agent-auto", owner="self",
             existing=(), recent_writes=()):
        seen["recent"] = list(recent_writes)
        return original(self, content, tags, source, owner, existing, recent_writes)

    monkeypatch.setattr(memory_policy.MemoryWritePolicy, "decide", _spy)
    store = PersistentMemoryStore(tmp_path / "data" / "pm.jsonl")
    memory_door_verdict(store, MemoryWritePolicy(), _PROSE, "[ВЫВОД, проверен боем]", "test:w4")

    memory_door_verdict(
        store, MemoryWritePolicy(), _PROSE + " Второй раз.",
        "[ВЫВОД, проверен боем]", "test:w4",
    )

    assert seen["recent"], "the second knock saw no recent writes"


# ── W5 ───────────────────────────────────────────────────────────────────────


def test_the_egress_host_lists_are_documented() -> None:
    config = Path("docs/CONFIGURATION.md").read_text(encoding="utf-8")
    example = Path(".env.example").read_text(encoding="utf-8")
    for name in ("AGENT_FETCH_ALLOW_HOSTS", "AGENT_FETCH_DENY_HOSTS"):
        assert name in config and name in example, name
