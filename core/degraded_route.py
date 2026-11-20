"""Notice that a different model than the routed one wrote the answer."""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

#: Label the router puts on a call that went to a fallback provider.
FAILOVER_PREFIX = "provider_failover:"
HEALTH_PREFIX = "provider_unhealthy:"

#: Human part of the provider error. It holds the repr of an SDK dict where the message lives; if the format changes, the exception class remains.
_MESSAGE_RE = re.compile(r"'message':\s*'([^']{4,400})'")

#: The answer tail is read by the operator, not the journal: a long sheet would crowd out what it is for.
_MAX_REASON = 160

#: Fixed start of the line: `format_human_response` assembles the answer by sections and drops anything not recognised by a fixed prefix (see `TAIL_PREFIX`).
NOTICE_PREFIX = "⚠️ Отвечала запасная модель"


@dataclass(frozen=True)
class SubstitutedRoute:
    """One role served by someone other than the chosen provider."""

    role: str
    intended: str
    answered: str
    refusals: int
    reason: str


def _name(record: Any) -> str:
    return f"{getattr(record, 'provider', '') or '?'}/{getattr(record, 'model', '') or '?'}"


def _short_reason(error: str | None) -> str:
    if not error:
        return ""
    match = _MESSAGE_RE.search(error)
    text = match.group(1) if match else error.split(":", 1)[0]
    text = " ".join(text.split())
    # The trailing period is stripped: the reason lands mid-sentence and would double it.
    return text[:_MAX_REASON].rstrip().rstrip(".")


def substituted_routes(records: Any, *, run_id: str | None = None) -> tuple[SubstitutedRoute, ...]:
    """Roles whose answer a fallback provider wrote; `run_id` narrows to the current turn because the ledger outlives a turn."""
    rows = [
        r for r in (records or [])
        if run_id is None or getattr(r, "run_id", None) in (None, run_id)
    ]
    out: dict[str, SubstitutedRoute] = {}
    for row in rows:
        if getattr(row, "status", "") != "success":
            continue
        route_reason = str(getattr(row, "route_reason", "") or "")
        if not route_reason.startswith((FAILOVER_PREFIX, HEALTH_PREFIX)):
            continue
        role = str(getattr(row, "role", "") or "?")
        if route_reason.startswith(HEALTH_PREFIX):
            origin, _, health = route_reason.removeprefix(HEALTH_PREFIX).partition(":")
            health = health.split("|", 1)[0].rsplit("->", 1)[0]
            out[role] = SubstitutedRoute(
                role, origin or "основной поставщик", _name(row), 0, health[:_MAX_REASON],
            )
            continue
        refused = [
            r for r in rows
            if getattr(r, "role", None) == role and getattr(r, "status", "") == "error"
        ]
        if not refused:
            # A substitution with no refusal this turn means the refusal came earlier and the router stayed on the fallback; still reported (silence is the defect), but with no reason.
            out[role] = SubstitutedRoute(role, "основная модель", _name(row), 0, "")
            continue
        out[role] = SubstitutedRoute(
            role=role,
            intended=_name(refused[0]),
            answered=_name(row),
            refusals=len(refused),
            reason=_short_reason(getattr(refused[-1], "error", None)),
        )
    return tuple(out.values())


def substitution_notice(
    routes: tuple[SubstitutedRoute, ...], *, rejected_draft: bool = False,
) -> str:
    """One line for the answer tail; empty when there was no substitution."""
    if not routes:
        return ""
    answered = sorted({r.answered for r in routes})
    intended = sorted({r.intended for r in routes})
    refusals = sum(r.refusals for r in routes)
    reason = next((r.reason for r in routes if r.reason), "")
    notice = (
        f"{NOTICE_PREFIX} {', '.join(answered)} — основная "
        f"{', '.join(intended)} недоступна"
    )
    if refusals:
        # "refusals: N" rather than "N refusals": avoids number inflection.
        notice += f" (отказов: {refusals})"
    if reason:
        notice += f": {reason}"
    if rejected_draft:
        return (
            notice + ". Модель подготовила отклонённый черновик; "
            "уведомление об отказе сформировано проверкой ответа."
        )
    return notice + ". Качество этого ответа — её, а не основной."
