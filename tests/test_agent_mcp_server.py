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

import ast
import importlib.util
import json
from pathlib import Path

import pytest

_REPO = Path(__file__).resolve().parents[1]
_VIEW = _REPO / "tools" / "agent_state_view.py"
_TRANSPORT = _REPO / "tools" / "agent_mcp_server.py"

#: Every name that mutates something, as it would appear in a call. Checked
#: against the server's AST rather than its behaviour: a write reached only on
#: a rare branch would never show up in a functional test.
_MUTATORS = (
    "write_text", "write_bytes", "open", "unlink", "mkdir", "rmdir", "remove",
    "rename", "replace", "touch", "append_state_jsonl_unlocked",
    "rewrite_state_jsonl_unlocked", "add", "save", "save_many", "update",
    "delete", "mark_running", "mark_failed", "approve", "enqueue",
)



def _opens_for_writing(call: ast.Call) -> bool:
    """Is this `open(...)` a write? Fails CLOSED on anything it cannot read.

    Three ways this was wrong before review round #317, all in the direction
    that lets a write through — the only direction that matters in a guard:

    * It flagged EVERY `open`, contradicting its own comment. Fixed first,
      because streaming a 5 MB journal needs `open`.
    * It read the mode from argument 1, which is right for the builtin
      `open(path, mode)` and WRONG for `path.open(mode)`, where the mode is
      argument 0. Since this file's own reader uses `path.open`, a
      `path.open("w")` would have passed unseen.
    * A mode it could not evaluate — `open(p, mode=chosen)` — counted as
      read. A gate that shrugs at what it does not understand is not a gate.

    Now: the mode position follows the call shape, and a present-but-unreadable
    mode is treated as a write. A caller who means read can pass the literal
    `"r"` and say so.
    """
    # `path.open(mode)` vs `open(path, mode)` — an attribute call puts the
    # mode first because the path is the receiver.
    mode_index = 0 if isinstance(call.func, ast.Attribute) else 1
    candidates: list[ast.expr] = []
    if len(call.args) > mode_index:
        candidates.append(call.args[mode_index])
    candidates.extend(kw.value for kw in call.keywords if kw.arg == "mode")
    if not candidates:
        return False              # no mode at all is "r"
    for node in candidates:
        if not (isinstance(node, ast.Constant) and isinstance(node.value, str)):
            return True          # unreadable mode: fail closed
        if any(ch in node.value for ch in "wax+"):
            return True
    return False


def _module():
    spec = importlib.util.spec_from_file_location("agent_state_view", _VIEW)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_the_server_is_registered_for_sessions_in_this_repository():
    config = json.loads((_REPO / ".mcp.json").read_text(encoding="utf-8"))
    entry = config["mcpServers"]["agent-state"]
    assert entry["args"] == ["tools/agent_mcp_server.py"], entry
    assert Path(_REPO / entry["args"][0]).is_file()


def test_nothing_in_the_server_can_write():
    """Read-only by inspection, not by intention.

    `subprocess.run` is allowed and is the one call that leaves the process —
    it runs `agent_tick.py --status`, which the agent's own CLI already treats
    as a read. Everything else that could mutate a store must be absent.
    """
    tree = ast.parse(_VIEW.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _MUTATORS and name != "open":
            found.append(name)
            continue
        if name == "open" and _opens_for_writing(node):
            found.append("open(mode=write)")
    assert not found, f"в сервере появились изменяющие вызовы: {sorted(set(found))}"


def test_a_missing_store_is_an_error_not_an_empty_list():
    module = _module()
    result = module._read_jsonl(_REPO / "data" / "no_such_store.jsonl")
    assert result["error"] == "missing"
    assert result["store"] == "no_such_store.jsonl"
    assert "rows" not in result, (
        "нечитаемое хранилище вернулось как пустое — читатель не отличит "
        "«ничего нет» от «не смог прочитать»"
    )


def test_an_unparseable_row_is_counted_not_dropped(tmp_path: Path):
    store = tmp_path / "probe.jsonl"
    store.write_text('{"a": 1}\nnot json at all\n{"a": 2}\n', encoding="utf-8")
    module = _module()

    result = module._read_jsonl(store)

    assert result["total"] == 2
    assert result["unreadable_rows"] == 1, (
        "строка, которую не разобрать, исчезла без счёта"
    )


def test_the_task_view_shows_both_queues():
    """This repository has two, and they are not the same one.

    `agent_tick.py` writes `data/task_queue.jsonl`; `app/bootstrap.py` and the
    health commands read `data/runtime_tasks.jsonl`. Showing one would let a
    reader conclude "nothing queued" while the other side is busy — so both are
    returned, labelled by who writes them.
    """
    module = _module()
    view = module.task_queue()
    assert set(view) == {"daemon", "repl_and_health"}, sorted(view)


@pytest.mark.parametrize("tool", ["task_queue", "approval_inbox",
                                  "recent_episodes", "run_journal",
                                  "open_defects"])
def test_every_tool_answers_without_raising(tool: str):
    """A tool that raises inside an MCP call gives the caller a stack, not a fact."""
    module = _module()
    result = getattr(module, tool)()
    assert isinstance(result, dict), (tool, type(result))


def test_the_transport_holds_no_logic_of_its_own():
    """Everything testable lives where CI can reach it without `mcp` installed.

    `mcp` is not a project requirement — it brings 18 direct dependencies
    including a web-server stack, and this repository pins, locks and SBOMs
    everything it ships. So the transport must stay a binding: if reading logic
    migrates back into it, CI can no longer test that logic at all, and the
    guard above would be scanning an empty file while the real code went
    unchecked.
    """
    tree = ast.parse(_TRANSPORT.read_text(encoding="utf-8"))
    defined = [n.name for n in ast.walk(tree)
               if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))]
    assert not defined, f"в транспорте появилась логика: {defined}"


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


def test_the_write_guard_still_catches_a_write():
    """The relaxation must not have turned the guard off.

    Both call shapes, because they put the mode in different places, and the
    method form is the one this file's own reader uses: an earlier version
    read argument 1 only, so `path.open("w")` passed unseen. And an
    unevaluable mode counts as a write — a gate that shrugs at what it cannot
    read is not a gate.
    """
    for source, expected in (
        ("open(p)", False),
        ('open(p, "r")', False),
        ('open(p, encoding="utf-8")', False),
        ('open(p, "w")', True),
        ('open(p, "a")', True),
        ('open(p, "r+")', True),
        ('open(p, mode="wb")', True),
        ("open(p, mode=chosen_at_runtime)", True),
        ("path.open()", False),
        ('path.open("r")', False),
        ('path.open(encoding="utf-8")', False),
        ('path.open("w")', True),
        ("path.open(mode_from_a_variable)", True),
    ):
        call = ast.parse(source).body[0].value
        assert _opens_for_writing(call) is expected, source


def test_a_large_journal_is_not_loaded_whole():
    """Reading is line by line: the biggest log here is 5 MB and growing.

    Checked structurally, because a functional test on a small file passes
    either way — `read_text().splitlines()` is the defect, and it is invisible
    until the file is large enough to hurt.

    By AST, not by searching the source text: the first version of this test
    matched `read_text(` and went red on the DOCSTRING that explains why
    `read_text` was removed. Judging code by resemblance to a string is the
    same mistake as an audit that cannot see through a name.
    """
    tree = ast.parse(_VIEW.read_text(encoding="utf-8"))
    reader = next(n for n in ast.walk(tree)
                  if isinstance(n, ast.FunctionDef) and n.name == "_read_jsonl")
    whole_file = [
        node.func.attr for node in ast.walk(reader)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        and node.func.attr in ("read_text", "read_bytes", "readlines")
    ]
    assert not whole_file, (
        f"хранилище снова читается целиком в память: {whole_file}"
    )


def test_only_the_last_rows_are_held_when_a_limit_is_given(tmp_path: Path):
    """`last=N` must cost N rows of memory, not the whole file.

    Behavioural half of the guard above: a bounded `deque` returns exactly the
    tail, and `total` still counts every row — "the last 3 of 500" and "the
    last 3 of 3" are different facts about the same answer.
    """
    store = tmp_path / "big.jsonl"
    store.write_text(
        "".join(json.dumps({"i": i}) + chr(10) for i in range(500)),
        encoding="utf-8",
    )
    module = _module()

    result = module._read_jsonl(store, last=3)

    assert result["total"] == 500
    assert [r["i"] for r in result["rows"]] == [497, 498, 499]


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


def test_the_journal_streams_instead_of_materialising_the_log():
    """The consumer must stream too, not only the reader beneath it.

    The first fix made `_read_jsonl` stream and then called it with `last=0`
    from `run_journal`, which kept every row and filtered into a second list:
    the plumbing was fixed and the one tap that pours 5 MB was left open.
    Checked by AST, since a small fixture behaves identically either way.
    """
    tree = ast.parse(_VIEW.read_text(encoding="utf-8"))
    journal = next(n for n in ast.walk(tree)
                   if isinstance(n, ast.FunctionDef) and n.name == "run_journal")
    calls = [n.func.id for n in ast.walk(journal)
             if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)]
    assert "_read_jsonl" not in calls, (
        "run_journal снова читает через материализующий вызов"
    )
    assert any(
        isinstance(n, ast.Call) and isinstance(n.func, ast.Attribute)
        and n.func.attr == "open"
        for n in ast.walk(journal)
    ), "run_journal перестал читать файл потоком"


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
