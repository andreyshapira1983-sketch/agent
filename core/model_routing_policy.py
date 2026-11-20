"""The agent's own routing policy: which model answers which role and why; decisions are journaled without secrets."""
# Layering in ModelRouter.route_for: agent policy -> env pins -> registry selection policy -> default. A record whose provider lost
# its key falls through to the next layer, never to an uncredentialed call. This module stores the operator's decisions and
# validates their form; it never ranks models in code.
from __future__ import annotations

import os
import re
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.state_integrity import append_state_jsonl, read_state_jsonl

DEFAULT_ROUTING_POLICY_PATH = "data/model_routing_policy.jsonl"

#: The roles a route can be set for — the router's own closed list.
_KNOWN_ROLES: frozenset[str] = frozenset(
    {"planner", "synthesizer", "repair_proposal", "memory_summary", "verifier"}
)

#: A reason must explain, not leak: anything shaped like a credential is refused before it reaches the journal.
_SECRET_RE = re.compile(
    r"(sk-[A-Za-z0-9_-]{8,}|AIza[0-9A-Za-z_-]{20,}|hf_[A-Za-z0-9]{10,}|"
    r"ghp_[A-Za-z0-9]{10,}|xai-[A-Za-z0-9]{10,}|[A-Za-z0-9_-]{40,})"
)

MAX_REASON_CHARS = 600
MAX_EVIDENCE_LINES = 8


@dataclass(frozen=True)
class RoutingChoice:
    """One active decision: this role is answered by this provider/model."""

    id: str
    role: str
    provider: str
    model: str
    reason: str
    evidence: tuple[str, ...] = ()
    set_by: str = "agent"
    ts: str = ""

    @property
    def route_reason(self) -> str:
        """What the usage ledger carries for every call routed by this record."""
        return f"agent_policy:{self.id}"


@dataclass
class RoutingVerdict:
    """The store's answer to a `set_route`: stored, or refused by name."""

    stored: bool
    choice: RoutingChoice | None = None
    refused_by: str = ""
    warnings: list[str] = field(default_factory=list)


def allowed_pool(env: dict[str, str] | None = None) -> tuple[str, ...]:
    """Providers the agent may route to: those the router can build a client for whose credentials are present; AGENT_ROUTING_POOL only narrows it."""
    from core.model_router import _DEFAULT_PROVIDER_ENV

    env = dict(os.environ) if env is None else env
    lowered = {k.lower(): v for k, v in env.items()}
    pool: list[str] = []
    for provider, names in _DEFAULT_PROVIDER_ENV.items():
        if provider == "mock" or not names:
            continue
        if all((lowered.get(name.lower()) or "").strip() for name in names):
            pool.append(provider)
    narrow = (env.get("AGENT_ROUTING_POOL") or "").strip().lower()
    if narrow:
        wanted = {p.strip() for p in narrow.split(",") if p.strip()}
        pool = [p for p in pool if p in wanted]
    return tuple(pool)


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class RoutingPolicyStore:
    """Append-only journal of routing decisions under the integrity envelope; the newest record per role wins, active=false releases the role."""

    def __init__(self, path: Path | str = DEFAULT_ROUTING_POLICY_PATH):
        self.path = Path(path)


    def _rows(self) -> list[dict[str, Any]]:
        if not self.path.is_file():
            return []
        try:
            return read_state_jsonl(self.path)
        except (OSError, ValueError):
            return []

    def version(self) -> tuple[int, int]:
        """Cheap change stamp so a router can drop cached clients when the policy moves."""
        try:
            st = self.path.stat()
        except OSError:
            return (0, 0)
        return (st.st_mtime_ns, st.st_size)

    def resolve(self, role: str) -> RoutingChoice | None:
        """The active choice for role, or None when none was made or it was released."""
        role = str(role or "").strip().lower()
        latest: dict[str, Any] | None = None
        for row in self._rows():
            if str(row.get("role") or "") == role:
                latest = row
        if not latest or not latest.get("active", True):
            return None
        return RoutingChoice(
            id=str(latest.get("id") or ""), role=role,
            provider=str(latest.get("provider") or ""), model=str(latest.get("model") or ""),
            reason=str(latest.get("reason") or ""),
            evidence=tuple(str(e) for e in (latest.get("evidence") or ())),
            set_by=str(latest.get("set_by") or "agent"), ts=str(latest.get("ts") or ""),
        )


    def history(self, role: str | None = None, *, limit: int = 20) -> list[dict[str, Any]]:
        rows = [r for r in self._rows() if role is None or r.get("role") == role]
        return rows[-limit:]


    def set_route(
        self, *, role: str, provider: str, model: str, reason: str,
        evidence: tuple[str, ...] | list[str] = (), set_by: str = "agent",
        env: dict[str, str] | None = None,
    ) -> RoutingVerdict:
        """Record a decision after validating its FORM (known role, provider in pool, model named, reason present and secret-free); substance is not judged."""
        role = str(role or "").strip().lower()
        provider = str(provider or "").strip().lower()
        model = str(model or "").strip()
        reason = " ".join(str(reason or "").split())
        if role not in _KNOWN_ROLES:
            return RoutingVerdict(False, refused_by=f"unknown role {role!r}; known: {', '.join(sorted(_KNOWN_ROLES))}")
        pool = allowed_pool(env)
        if provider not in pool:
            return RoutingVerdict(False, refused_by=(
                f"provider {provider!r} is not in the allowed pool "
                f"{list(pool) or '[]'} (a provider needs a client in this code and a present key)"
            ))
        if not model or len(model) > 80 or any(ch.isspace() for ch in model):
            return RoutingVerdict(False, refused_by="model must be one non-empty token (≤80 chars)")
        if not reason:
            return RoutingVerdict(False, refused_by="reason is required: a route without a why cannot be explained later")
        if len(reason) > MAX_REASON_CHARS:
            return RoutingVerdict(False, refused_by=f"reason longer than {MAX_REASON_CHARS} chars")
        if _SECRET_RE.search(reason) or any(_SECRET_RE.search(str(e)) for e in evidence):
            return RoutingVerdict(False, refused_by="reason/evidence carries a secret-looking token; routes are journaled without secrets")
        warnings: list[str] = []
        lines = [" ".join(str(e).split())[:300] for e in evidence if str(e).strip()]
        if len(lines) > MAX_EVIDENCE_LINES:
            warnings.append(f"evidence trimmed to {MAX_EVIDENCE_LINES} lines")
            lines = lines[:MAX_EVIDENCE_LINES]
        if not lines:
            warnings.append("no evidence cited: a measured policy names what it measured")
        choice = RoutingChoice(
            id=f"route_{uuid.uuid4().hex[:12]}", role=role, provider=provider, model=model,
            reason=reason, evidence=tuple(lines), set_by=set_by, ts=_now(),
        )
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_state_jsonl(self.path, [{
            "id": choice.id, "ts": choice.ts, "role": role, "provider": provider,
            "model": model, "reason": reason, "evidence": list(lines),
            "set_by": set_by, "active": True,
        }])
        return RoutingVerdict(True, choice=choice, warnings=warnings)

    def release_route(self, *, role: str, reason: str, set_by: str = "agent") -> RoutingVerdict:
        """Hand the role back to the env/registry layers, with a why."""
        role = str(role or "").strip().lower()
        reason = " ".join(str(reason or "").split())
        if role not in _KNOWN_ROLES:
            return RoutingVerdict(False, refused_by=f"unknown role {role!r}")
        if not reason:
            return RoutingVerdict(False, refused_by="reason is required")
        if _SECRET_RE.search(reason):
            return RoutingVerdict(False, refused_by="reason carries a secret-looking token")
        self.path.parent.mkdir(parents=True, exist_ok=True)
        append_state_jsonl(self.path, [{
            "id": f"route_{uuid.uuid4().hex[:12]}", "ts": _now(), "role": role,
            "provider": "", "model": "", "reason": reason, "evidence": [],
            "set_by": set_by, "active": False,
        }])
        return RoutingVerdict(True)




def agent_policy_route(store: Any, role_key: str) -> Any | None:
    """The agent's own decision for this role as a ModelRoute if made and its provider still has a key, else None (falls through to the next layer)."""
    if store is None:
        return None
    from core.model_router import (
        _DEFAULT_PROVIDER_ENV,
        ModelRoute,
        _normalise_provider,
        _provider_has_credentials,
    )

    try:
        choice = store.resolve(role_key)
    except Exception:  # noqa: BLE001 — an unreadable policy is «no policy», never a crash
        return None
    if choice is None:
        return None
    provider = _normalise_provider(choice.provider)
    if provider not in _DEFAULT_PROVIDER_ENV or not _provider_has_credentials(provider):
        return None
    return ModelRoute(role=role_key, provider=provider, model=choice.model, reason=choice.route_reason)


def drop_clients_if_policy_moved(
    store: Any, last_stamp: tuple[int, int] | None, cache: dict[str, Any],
) -> tuple[int, int] | None:
    """Drop the router's cached per-role clients when the policy file changed; returns the stamp to remember."""
    if store is None:
        return last_stamp
    try:
        stamp = store.version()
    except Exception:  # noqa: BLE001 — a stat that fails keeps the cache
        return last_stamp
    if stamp != last_stamp:
        cache.clear()
    return stamp
