"""What the agent knows about itself by MEASUREMENT, not from a prompt hint."""
from __future__ import annotations

from collections.abc import Iterable, Mapping
from contextvars import ContextVar
from types import MappingProxyType
from typing import Any

#: What the block SHOWED the model in this run, so the verifier accepts exactly the fields `[runtime:<field>]` may cite.
_SHOWN: ContextVar[Mapping[str, str]] = ContextVar("runtime_self_shown", default=MappingProxyType({}))


def shown_facts(run_id: str) -> dict[str, str]:
    """Fields shown by the block in run `run_id`; another run gives empty."""
    facts = _SHOWN.get()
    return dict(facts) if run_id and facts.get("run_id") == run_id else {}

#: Order is fixed so the reader gets the same list every turn ("organ not named" vs "no organ" must stay distinct).
_ORGAN_ORDER: tuple[str, ...] = (
    "working_memory",
    "persistent_store",
    "episodic_store",
    "procedural_store",
    "source_registry_store",
    "user_profile_store",
    "approval_provider",
)


def process_facts() -> dict[str, str]:
    """What the agent RUNS on: measured, not derived."""
    import os
    import platform
    import sys

    return {
        "interpreter": sys.executable or "(unknown)",
        "python_version": platform.python_version(),
        "platform": sys.platform,
        "pid": str(os.getpid()),
        "cwd": os.getcwd(),
    }


def runtime_self_block(
    *,
    trace_id: str,
    run_id: str,
    session_id: str | None,
    stores: Mapping[str, Any],
    durable_writes: Iterable[str] | None,
    tools: Iterable[str] | None = None,
) -> str:
    """Facts about the current execution, checkable against the journal.

    Empty string when the run identity is unknown: inventing it is inventing a personality, only smaller.
    """
    if not trace_id or not run_id:
        _SHOWN.set({})
        return ""
    shown = {"run_id": run_id, "trace_id": trace_id, "session_id": session_id or "нет"}

    lines = [
        "<runtime_self>",
        "Проверяемые факты об этом исполнении. Не описание личности:",
        "чем ты являешься, здесь не сказано — сказано, что подключено.",
        f"  run_id={run_id} trace_id={trace_id} session_id={session_id or 'нет'}",
    ]

    # body before organs: "what I run on" is primary to "what is attached to me"; cited as [runtime:<field>]
    for key, value in process_facts().items():
        lines.append(f"  {key}: {value}")

    named = set(stores)
    for organ in (*_ORGAN_ORDER, *sorted(named - set(_ORGAN_ORDER))):
        if organ not in named:
            continue
        state = "подключён" if stores.get(organ) is not None else "нет"
        lines.append(f"  {organ}: {state}")
        shown[organ] = state

    # `None` = attended session where ALL sinks are allowed (app/bootstrap.build_agent contract); it must not print as "nothing allowed"
    if durable_writes is None:
        writes = "все приёмники (присутственная сессия)"
    else:
        allowed = sorted(str(w) for w in durable_writes)
        writes = ", ".join(allowed) if allowed else "ничего не разрешено"
    lines.append(f"  durable_writes: {writes}")
    # an action without a tool (mail, call, payment) is "no": a measured fact about itself
    if tools is not None:
        shown["tools"] = ", ".join(sorted(tools)) or "нет"
        lines.append(f"  tools: {shown['tools']}")
    lines.append("</runtime_self>")
    _SHOWN.set({**shown, "durable_writes": writes})
    return "\n".join(lines)
