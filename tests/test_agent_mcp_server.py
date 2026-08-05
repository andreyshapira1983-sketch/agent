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
_SERVER = _REPO / "tools" / "agent_mcp_server.py"

#: Every name that mutates something, as it would appear in a call. Checked
#: against the server's AST rather than its behaviour: a write reached only on
#: a rare branch would never show up in a functional test.
_MUTATORS = (
    "write_text", "write_bytes", "open", "unlink", "mkdir", "rmdir", "remove",
    "rename", "replace", "touch", "append_state_jsonl_unlocked",
    "rewrite_state_jsonl_unlocked", "add", "save", "save_many", "update",
    "delete", "mark_running", "mark_failed", "approve", "enqueue",
)


def _module():
    spec = importlib.util.spec_from_file_location("agent_mcp_server", _SERVER)
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
    tree = ast.parse(_SERVER.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        func = node.func
        name = func.attr if isinstance(func, ast.Attribute) else getattr(func, "id", "")
        if name in _MUTATORS and name != "open":
            found.append(name)
        # `open` is only a write when a mode says so; `read_text` is used here.
        if name == "open":
            found.append("open")
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
