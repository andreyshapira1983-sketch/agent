"""Short unique identifiers for trace correlation."""
from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """Generate a short prefixed id, e.g. obs_a1b2c3d4e5f60708."""
    return f"{prefix}_{secrets.token_hex(8)}"
