"""The MCP view must stay read-only, and must never report silence for an error.

Operator question 2026-08-05: can the autonomous agent and an assistant see the
same state instead of losing each other between sessions? The answer is a
server the agent exposes and the assistant connects to. These tests guard the
two properties that make it safe to leave running.

**Read-only.** Not "read-only for now" — an autonomous agent that can ask an
assistant which edits code and runs commands is a loop with no human in it, and
this repository already owns the gate for that (`ActuationGateway`, the
approval inbox). A write path appearing here would sit beside that gate rather
than behind it, so the absence of one is checked, not assumed.

**No silent empties.** A store that will not open must be distinguishable from
a store that is empty. That is MIR-077 applied to the reader: an assistant told
"no pending tasks" when the file was unreadable will act on a fact that was
never established.
"""
from __future__ import annotations

import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_VIEW = _REPO / "tools" / "agent_state_view.py"


def _module():
    spec = importlib.util.spec_from_file_location("agent_state_view", _VIEW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_task_view_reads_the_one_store(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """The view counts runtime_tasks.jsonl by status and lists its newest 20 tasks.

    Runs on a store it writes: an error answer from a missing data/ would prove nothing.
    """
    module = _module()
    data = tmp_path / "data"
    data.mkdir()
    rows = [json.dumps({"id": f"t{i}", "status": "done"}) for i in range(21)]
    rows.append(json.dumps({"_integrity": {"sig": "x"}, "payload": {
        "id": "t21", "status": "failed", "attempts": 3, "last_error": "timeout"}}))
    rows.append("not json")
    (data / "runtime_tasks.jsonl").write_text(chr(10).join(rows) + chr(10), encoding="utf-8")
    monkeypatch.setattr(module, "_DATA", data)

    view = module.task_queue()

    assert "error" not in view, view
    assert "daemon" not in view and "repl_and_health" not in view, sorted(view)
    assert view["store"] == "runtime_tasks.jsonl"
    assert view["by_status"] == {"done": 21, "failed": 1}
    assert view["unreadable_rows"] == 1
    assert [t["id"] for t in view["tasks"]] == [f"t{i}" for i in range(2, 22)]
    assert view["tasks"][-1]["last_error"] == "timeout"


@pytest.mark.parametrize("tool", ["task_queue", "approval_inbox",
                                  "recent_episodes", "run_journal"])
def test_every_tool_names_a_missing_store(tool: str, tmp_path: Path,
                                          monkeypatch: pytest.MonkeyPatch):
    """With no data/ and no logs/, every view answers with a named `missing` error.

    Raising hands an MCP caller a stack; an empty answer reads as "nothing there".
    """
    module = _module()
    monkeypatch.setattr(module, "_DATA", tmp_path / "data")
    monkeypatch.setattr(module, "_LOGS", tmp_path / "logs")

    result = getattr(module, tool)()

    assert result.get("error") == "missing", (tool, result)


def test_the_inbox_view_lists_only_pending_items(tmp_path: Path,
                                                 monkeypatch: pytest.MonkeyPatch):
    """Only `pending` items, unwrapped from their envelope, count as what the agent waits on."""
    module = _module()
    data = tmp_path / "data"
    data.mkdir()
    rows = [
        {"_integrity": {"sig": "x"}, "payload": {
            "id": "a1", "operation": "self_apply_lane.run", "summary": "wait",
            "risk": "low", "status": "pending"}},
        {"id": "a2", "status": "denied", "summary": "no"},
        {"id": "a3", "status": "approved", "summary": "yes"},
        {"id": "a4", "status": "pending", "summary": "also wait"},
    ]
    (data / "approval_inbox.jsonl").write_text(
        "".join(json.dumps(r) + chr(10) for r in rows) + "broken" + chr(10),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_DATA", data)

    view = module.approval_inbox()

    assert view["pending_count"] == 2, view
    assert view["total"] == 4
    assert view["unreadable_rows"] == 1
    assert [p["id"] for p in view["pending"]] == ["a1", "a4"]
    assert view["pending"][0]["operation"] == "self_apply_lane.run"


def test_the_episode_view_returns_the_newest_episodes(tmp_path: Path,
                                                      monkeypatch: pytest.MonkeyPatch):
    """`limit` keeps the newest episodes, unwrapped; `total` still counts every one."""
    module = _module()
    data = tmp_path / "data"
    data.mkdir()
    (data / "episodic_memory.jsonl").write_text(
        "".join(
            json.dumps({"payload": {"id": f"e{i}", "question": f"q{i}", "outcome": "ok"}})
            + chr(10)
            for i in range(5)
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_DATA", data)

    view = module.recent_episodes(limit=2)

    assert view["total"] == 5
    assert [e["id"] for e in view["episodes"]] == ["e3", "e4"]
    assert view["episodes"][-1]["question"] == "q4"


def test_the_status_view_captures_the_stream_the_agent_actually_writes():
    """`_print_status` writes to stderr; a stdout-only capture returns nothing.

    Measured, after exactly that mistake: the redirect caught zero characters
    while the text still reached the terminal, so the viewer would have
    reported "(no output)" about silence it created itself.
    """
    module = _module()
    status = module.agent_status()
    assert status != "(no output)"
    assert "Daemon:" in status, status[:200]


def test_the_journal_reports_matches_and_the_raw_total_separately(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
):
    """A filtered view must not present the file's size as its match count.

    `events_total` used to be the row count of the whole log whatever the
    filter, so a caller asking for errors saw a large number beside three
    events and could not tell an empty filter from an empty run.

    Runs against a journal it writes itself. The first version read the
    developer's `logs/` and passed here while failing in CI, which has no such
    directory — notebook §32, a test that passes because of where you ran it.
    """
    module = _module()
    logs = tmp_path / "logs"
    logs.mkdir()
    (logs / "run_probe.jsonl").write_text(
        "".join(
            json.dumps({"event": name, "payload": {}}) + chr(10)
            for name in ("plan", "act", "plan", "respond")
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(module, "_LOGS", logs)

    everything = module.run_journal(limit=5)
    only_plans = module.run_journal(limit=5, event_filter="plan")
    nothing = module.run_journal(limit=5, event_filter="no_such_event")

    assert everything["rows_in_log"] == 4
    assert everything["events_matched"] == 4
    assert only_plans["events_matched"] == 2, "фильтр не сузил счёт"
    assert only_plans["rows_in_log"] == 4, "сырое число должно остаться сырым"
    assert nothing["events_matched"] == 0
    assert nothing["rows_in_log"] == 4, (
        "пустой фильтр и пустой журнал стали неотличимы"
    )


def test_the_journal_tail_is_bounded_by_matches_not_by_rows(tmp_path: Path,
                                                            monkeypatch):
    """`limit` counts MATCHED events, so a filter cannot be starved by noise."""
    module = _module()
    logs = tmp_path / "logs"
    logs.mkdir()
    lines = []
    for i in range(300):
        lines.append(json.dumps({"event": "noise", "payload": {"i": i}}))
        if i % 100 == 0:
            lines.append(json.dumps({"event": "plan", "payload": {"i": i}}))
    (logs / "run_probe.jsonl").write_text(chr(10).join(lines) + chr(10),
                                          encoding="utf-8")
    monkeypatch.setattr(module, "_LOGS", logs)

    plans = module.run_journal(limit=2, event_filter="plan")

    assert plans["rows_in_log"] == 303
    assert plans["events_matched"] == 3
    assert len(plans["events"]) == 2, "хвост должен считать совпадения, а не строки"
    assert [e["payload"]["i"] for e in plans["events"]] == [100, 200]


def test_the_defect_view_and_the_build_count_read_the_status_line_the_same_way():
    """One field, one reader — the view may not disagree with the tally.

    Measured 2026-08-14, the hour the registry was restored: the view carried
    its own regex, ``\\*\\*Status:\\*\\*\\s*`?(\\w+)`?``, which cannot match a
    bold-wrapped verdict. 16 entries write ``**Status:** **`fixed`**``; each
    parsed as ``?``, ``?`` is not in the closed set, so all 16 were reported
    OPEN and ``open_count`` said 55 where the registry held 39. The build
    checked the other parser and stayed green throughout.

    So the assertion is equality with the derived count, not a literal: a
    number written here would be the third opinion about the same field.
    """
    import sys

    scripts = str(_REPO / "scripts")
    if scripts not in sys.path:
        sys.path.insert(0, scripts)
    import registry_tally

    view = _module().open_defects()
    if "error" in view:                       # no registry in the tree
        pytest.skip(f"registry absent: {view}")

    by_status, unparsed = registry_tally.parse(
        registry_tally.REGISTRY.read_text(encoding="utf-8")
    )
    assert not unparsed, f"tally could not read a Status line: {unparsed}"
    assert view.get("unparsed", []) == [], (
        "the view failed to read a Status line and must say so rather than "
        f"file it as open: {view.get('unparsed')}"
    )

    closed = {"fixed", "diagnosis_corrected"}
    derived = sum(len(v) for s, v in by_status.items() if s not in closed)
    assert view["open_count"] == derived, (
        f"the MCP view reports {view['open_count']} open defects, the build's "
        f"own tally derives {derived} — two readers of one Status field"
    )
