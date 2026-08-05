#!/usr/bin/env python3
"""MCP transport for the read-only agent view. Nothing but binding lives here.

The operator's question, 2026-08-05: can the autonomous agent and an assistant
coordinate instead of losing each other between sessions? MCP runs
client -> server and Claude Code is a CLIENT, so the working direction is the
reverse of the intuitive one: the AGENT exposes a server, and the assistant
connects to it.

## Why this file is a dozen lines of substance

`mcp` brings 18 direct dependencies, among them a web-server stack (starlette,
uvicorn, sse-starlette, websockets), `pyjwt[crypto]`, `typer` and `rich`. This
repository pins every requirement exactly, locks them with hashes and ships an
SBOM -- its own supply-chain guard rejected `mcp>=1.27` on this branch, and it
was right to. A viewer that `core/` and `app/` never import does not justify
adding that surface to what the project ships.

So the reading lives in `tools/agent_state_view.py`, which imports nothing
outside the standard library and is tested with nothing installed, and this
file only wraps it. Install `mcp` on the machine that wants the view:

    pip install mcp

Run:  python tools/agent_mcp_server.py
Registered for sessions opened in this repository by `.mcp.json`.

## Read-only, and that is a design decision rather than a first version

An autonomous agent that can ask an assistant which edits code and runs
commands is a loop with no human in it. This repository already owns the right
gate -- `ActuationGateway` and the approval inbox -- and a write path added
here would sit beside that gate rather than behind it. The absence of one is
checked by AST in `tests/test_agent_mcp_server.py`, not assumed: a write
reached only on a rare branch would never surface in a functional test.
"""
from __future__ import annotations

import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO) not in sys.path:
    sys.path.insert(0, str(REPO))

from mcp.server.fastmcp import FastMCP  # noqa: E402

from tools.agent_state_view import (  # noqa: E402
    agent_status,
    approval_inbox,
    open_defects,
    recent_episodes,
    run_journal,
    task_queue,
)

mcp = FastMCP("agent-state")

# Registered by reference, not redefined: a wrapper here would be a second
# place for the view to drift from itself, and each function's docstring is
# what the assistant reads to decide whether to call it.
for _tool in (agent_status, task_queue, approval_inbox,
              recent_episodes, run_journal, open_defects):
    mcp.tool()(_tool)


if __name__ == "__main__":                                   # pragma: no cover
    mcp.run()
