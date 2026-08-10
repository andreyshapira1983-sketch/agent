"""Short unique identifiers for trace correlation."""
from __future__ import annotations

import secrets


def new_id(prefix: str) -> str:
    """Generate a prefixed id with 128-bit entropy, e.g. obs_<32 hex chars>.

    128 bits matches the W3C TraceContext / OpenTelemetry trace-id standard
    and eliminates birthday-collision risk in distributed logs.
    """
    return f"{prefix}_{secrets.token_hex(16)}"


# Приехало из `core/loop_helpers.py`: идентификатор трассы — это идентификатор,
# и его дом здесь, рядом с `new_id`, которым он и сделан.
def new_trace_id() -> str:
    """Идентификатор СЕАНСА: один журнал, сколько угодно прогонов внутри.

    Приставка была `run_`, и это враньё стоило дорого: файл `run_<hex>.jsonl`
    собирал все прогоны сеанса, так что «прогон» в имени опознавался как
    прогон. 2026-08-10 агент из-за этого не смог сказать, что именно
    исполняется. Ребро связи пишет `core/run_context.identity_provenance`.
    """
    return new_id("trace")
