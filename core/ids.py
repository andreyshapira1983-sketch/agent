"""Short unique identifiers for trace correlation."""
from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """Prefixed id with 128-bit entropy, e.g. obs_<32 hex chars> (W3C TraceContext size, no birthday-collision risk)."""
    return f"{prefix}_{secrets.token_hex(16)}"


def new_trace_id() -> str:
    """Id of a SESSION: one journal, any number of runs inside."""
    return new_id("trace")
