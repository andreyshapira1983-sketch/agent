#!/usr/bin/env python3
"""Read-only MCP server over tools/agent_state_view.py (needs ``pip install mcp``)."""
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

# Registered by reference, not wrapped: the assistant reads each view function's docstring.
for _tool in (agent_status, task_queue, approval_inbox,
              recent_episodes, run_journal, open_defects):
    mcp.tool()(_tool)


if __name__ == "__main__":                                   # pragma: no cover
    mcp.run()
