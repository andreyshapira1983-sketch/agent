"""Roster of models that spend the output budget on reasoning (the 'silent' ones), kept in its own journal (``data/reasoning_roster.jsonl``)."""
from __future__ import annotations

import os
from typing import Any

#: Home of the roster, set by the RUNTIME at the entry point (`ensure_roster_home`), never by this library; unset means no roster here.
ROSTER_RELPATH = "data/reasoning_roster.jsonl"


def _roster_path() -> Any:
    """Roster path from the environment; None means the runtime set no home."""
    from pathlib import Path

    configured = (os.getenv("AGENT_REASONING_ROSTER") or "").strip()
    return Path(configured) if configured else None


def ensure_roster_home(workspace: Any) -> Any:
    """Entry point declares the roster home unless the operator set one; returns the effective path."""
    from pathlib import Path

    configured = (os.getenv("AGENT_REASONING_ROSTER") or "").strip()
    if configured:
        return Path(configured)
    home = Path(workspace) / ROSTER_RELPATH
    os.environ["AGENT_REASONING_ROSTER"] = str(home)
    return home


def _roster_key(provider: str, model: str) -> str:
    return f"{(provider or '').strip().lower()}:{(model or '').strip()}"


def is_known_reasoning_model(provider: str, model: str) -> bool:
    """Has this model already proved it spends the budget on thinking?

    A missing or unreadable roster answers "not known" — ignorance must not
    become a verdict, and the caller simply pays the blind leg once more.
    """
    from core.state_integrity import read_state_jsonl

    path = _roster_path()
    if path is None or not path.exists():
        return False
    try:
        rows = read_state_jsonl(path)
    except Exception:  # noqa: BLE001 — a damaged roster must never break a call
        return False
    key = _roster_key(provider, model)
    return any(str(row.get("key")) == key for row in rows)


def remember_reasoning_model(provider: str, model: str, *, spent: int) -> None:
    """Record what silence taught us, so the next run does not buy it again."""
    from core.state_integrity import append_state_jsonl

    path = _roster_path()
    if path is None or is_known_reasoning_model(provider, model):
        return
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        append_state_jsonl(path, [{
            "key": _roster_key(provider, model),
            "provider": (provider or "").strip().lower(),
            "model": (model or "").strip(),
            "evidence": f"truncated with empty text at {int(spent)} output tokens",
        }])
    except Exception:  # noqa: BLE001 — learning is best-effort, answering is not
        return
