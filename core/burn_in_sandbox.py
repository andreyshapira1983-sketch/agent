"""Explicit sandbox authority: unattended self-repair of an isolated copy, enabled only by an env flag plus a marker file."""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.protected_paths import path_key

#: Env flag set by the process launcher.
SANDBOX_ENV_FLAG = "AGENT_BURN_IN_SANDBOX"

#: Marker file prepared with the copy (repo-relative path).
SANDBOX_MARKER = "config/burn_in_sandbox.json"

#: Values meaning "on"; anything else is off (fail-closed).
_ON_VALUES: frozenset[str] = frozenset({"on", "1", "true", "yes"})

#: Files that decide sandbox authority; part of core.protected_paths, compared by its path_key.
_FENCE: frozenset[str] = frozenset({
    SANDBOX_MARKER,
    "core/burn_in_sandbox.py",
    "core/burn_in_supervisor.py",
    "scripts/burn_in_supervisor.py",
    "scripts/autonomous_repair.py",
    "core/policy.py",
    "core/rule_approved_apply.py",
    "core/autonomous_runtime.py",
    "core/approval_inbox.py",
    "core/actuation_gateway.py",
    "core/safe_vcs.py",
    "core/self_apply_lane.py",
    "scripts/install_daemon.ps1",
})

_FENCE_KEYS: frozenset[str | None] = frozenset(map(path_key, _FENCE))

_DEFAULT_MAX_APPLIES_PER_DAY = 20

#: The only memory sinks the authority opens; authority-bearing sinks are never opened.
SANDBOX_DURABLE_SINKS: frozenset[str] = frozenset({"procedure", "knowledge"})


@dataclass(frozen=True)
class SandboxAuthority:
    """Enabled authority; the id prefix marks in the spend journal what allowed a change."""

    id: str
    workspace: str
    max_applies_per_day: int
    expires_at: str
    reason: str

    def is_live(self, now: datetime | None = None) -> bool:
        """True while unexpired; an unreadable timestamp counts as expired."""
        raw = str(self.expires_at or "").strip()
        if not raw:
            return False
        try:
            deadline = datetime.fromisoformat(raw)
        except ValueError:
            return False
        if deadline.tzinfo is None:
            deadline = deadline.replace(tzinfo=timezone.utc)
        return deadline > (now or datetime.now(timezone.utc))

    def to_dict(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "workspace": self.workspace,
            "max_applies_per_day": self.max_applies_per_day,
            "expires_at": self.expires_at,
            "reason": self.reason,
        }


def _flag_is_on(env: Any) -> bool:
    source = os.environ if env is None else env
    return str(source.get(SANDBOX_ENV_FLAG, "")).strip().lower() in _ON_VALUES


def _same_place(declared: str, workspace: Path) -> bool:
    """True when the marker names this copy by absolute path; a relative path would be portable to any tree."""
    try:
        if not Path(declared).is_absolute():
            return False
        return Path(declared).resolve() == Path(workspace).resolve()
    except (OSError, ValueError):
        return False


def _others_can_write(path: Path) -> bool:
    """True when the marker is world-writable (like OpenSSH StrictModes); group-write is allowed, Windows is not checked."""
    if os.name != "posix":
        return False
    try:
        return bool(path.stat().st_mode & 0o002)
    except OSError:
        return False


def load_sandbox_authority(
    workspace: Any, *, env: Any = None, now: datetime | None = None
) -> SandboxAuthority | None:
    """Return the sandbox authority, or None on any doubt (fail-closed, repairs nothing)."""
    if not _flag_is_on(env):
        return None
    root = Path(workspace or ".")
    marker = root / SANDBOX_MARKER
    if _others_can_write(marker):
        return None
    try:
        raw = json.loads(marker.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(raw, dict) or raw.get("sandbox") is not True:
        return None
    declared = str(raw.get("workspace") or "").strip()
    if not declared or not _same_place(declared, root):
        return None
    expires_at = str(raw.get("expires_at") or "").strip()
    if not expires_at:
        return None
    try:
        deadline = datetime.fromisoformat(expires_at)
    except ValueError:
        return None
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    if deadline <= (now or datetime.now(timezone.utc)):
        return None
    reason = str(raw.get("reason") or "").strip()
    if not reason:
        # An authority with no stated reason can be neither verified nor revoked.
        return None
    try:
        cap = int(raw.get("max_applies_per_day", _DEFAULT_MAX_APPLIES_PER_DAY))
    except (TypeError, ValueError):
        return None
    if cap <= 0:
        return None
    return SandboxAuthority(
        id=f"sandbox:{expires_at}",
        workspace=str(Path(root).resolve()),
        max_applies_per_day=cap,
        expires_at=expires_at,
        reason=reason,
    )


def sandbox_execution_verdict(proposal: Any, *, workspace: Any) -> tuple[bool, str]:
    """Return (ok, reason) for applying a proposal in the sandbox; wider than production only in file class."""
    from core.self_apply_lane import _is_allowed, _is_denied, _normalize_rel

    files = tuple(getattr(proposal, "files", ()) or ())
    if not files:
        return False, "no files in the proposal"
    root = Path(workspace).resolve()
    for change in files:
        raw = str(getattr(change, "path", "") or "")
        rel = _normalize_rel(raw)
        if rel is None:
            return False, f"{raw!r} points outside the sandbox workspace"
        target = (root / rel).resolve()
        if root != target and root not in target.parents:
            return False, f"{rel!r} resolves outside the sandbox workspace"
        # Fence and class are also checked on the resolved path: a symlink can point past the fence.
        names = [rel]
        try:
            resolved_rel = target.relative_to(root).as_posix()
        except ValueError:
            resolved_rel = rel
        if resolved_rel != rel:
            names.append(resolved_rel)
        for name in names:
            if path_key(name) in _FENCE_KEYS:
                return False, f"{name!r} is the sandbox fence; it is not moved from inside"
            if _is_denied(name):
                return False, f"{name!r} is a denied class (secrets, CI, infrastructure)"
        # Also check the lane allowlist, else a request takes a ceiling unit and the lane rejects it.
        for name in names:
            if not _is_allowed(name):
                return False, (
                    f"{name!r} is outside the lane allowlist; the lane would refuse it"
                )
    return True, "sandbox authority: workspace-local change, verified by the lane"


def sandbox_applies_today(workspace: Any, authority: SandboxAuthority) -> int:
    """Applies the sandbox has used today (UTC)."""
    from core.autonomous_runtime import standing_runs_today

    return standing_runs_today(workspace, authority.id)


def reserve_sandbox_apply(workspace: Any, authority: SandboxAuthority) -> bool:
    """Take one unit of the daily ceiling or refuse; shares the journal and atomic primitive with the standing grant."""
    from core.autonomous_runtime import reserve_standing_grant_use

    if not authority.is_live():
        return False
    return reserve_standing_grant_use(
        workspace, authority.id, max_per_day=authority.max_applies_per_day
    )


class TermedSinks(frozenset):
    """Sink set whose granted extension carries its own expiry; base sinks are not time-limited."""

    __slots__ = ("_deadline", "_granted")

    def __new__(cls, base, granted, deadline):
        self = super().__new__(cls, frozenset(base) | frozenset(granted))
        self._granted = frozenset(granted) - frozenset(base)
        self._deadline = deadline
        return self

    def contains_at(self, sink: object, now: datetime) -> bool:
        if not frozenset.__contains__(self, sink):
            return False
        if sink in self._granted and now >= self._deadline:
            return False
        return True

    def __contains__(self, sink: object) -> bool:
        return self.contains_at(sink, datetime.now(timezone.utc))


def memory_profile_for(base: dict, workspace: Any, *, env: Any = None) -> dict:
    """Unattended memory profile: a copy of base, plus SANDBOX_DURABLE_SINKS for the authority's term."""
    profile = dict(base)
    authority = load_sandbox_authority(workspace, env=env)
    if authority is None:
        return profile
    try:
        deadline = datetime.fromisoformat(authority.expires_at)
    except ValueError:  # pragma: no cover — the authority already parsed this string
        return profile
    if deadline.tzinfo is None:
        deadline = deadline.replace(tzinfo=timezone.utc)
    profile["durable_writes"] = TermedSinks(
        frozenset(profile.get("durable_writes") or ()),
        SANDBOX_DURABLE_SINKS,
        deadline,
    )
    return profile
