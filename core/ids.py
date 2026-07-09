"""Short unique identifiers for trace correlation."""
from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """Generate a prefixed id with 128-bit entropy, e.g. obs_<32 hex chars>.

    128 bits matches the W3C TraceContext / OpenTelemetry trace-id standard
    and eliminates birthday-collision risk in distributed logs.
    """
    return f"{prefix}_{secrets.token_hex(16)}"
