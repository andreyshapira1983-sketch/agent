"""Subagent role performance ledger (TD-028).

A small, persistent read/write registry that records how the self-build
producer's role-level subagents — Manager, Researcher, Builder, Critic, Reporter
— perform over time, so the central agent can begin *managing a corporation of
agents*.

Scope of this module (deliberately narrow):

* It RECORDS and REPORTS role performance only. It never hires/fires: the
  ``recommendation`` field (keep / watch / pause / retire) is **advisory** and is
  NEVER used to change a role's stored ``status``. There is no auto-pause,
  auto-retire, or model-routing change here.
* It performs no LLM/provider/network/git work and never applies, commits, or
  pushes anything. It only reads/writes a single JSON file under ``data/``.
* Writes are best-effort: callers wrap ``record_*`` so a registry write failure
  can never break the producer or the daemon tick.

Decision mapping (TD-028 clarification 4/5/6):

* manager  ``selected``  -> success        ``no_target`` -> neutral (not failure)
* researcher ``gathered`` -> success
* builder  ``built``     -> success        ``failed``    -> failure
* critic   ``pass``      -> success        ``veto``      -> useful veto (NOT a
  failure; it increases the Critic's trust/usefulness).
* reporter ``published`` -> success

A Critic veto also marks the vetoed Builder output (``outputs_vetoed``) so that
repeated vetoed Builder outputs can move the Builder's *recommendation* toward
watch/pause/retire — but, again, only the recommendation, never the status.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

# Persistent ledger location (gitignored ``data/`` tree). One small JSON file
# keyed by role id; atomic tmp+replace on write.
REGISTRY_PATH = "data/subagent_registry.json"

# The producer's role-level subagents. Treated as ROLES for now (TD-028
# clarification 1), not yet fully independent persistent agents.
DEFAULT_ROLES: tuple[str, ...] = (
    "manager",
    "researcher",
    "builder",
    "critic",
    "reporter",
)

VALID_STATUSES: tuple[str, ...] = ("active", "paused", "retired")
VALID_RECOMMENDATIONS: tuple[str, ...] = ("keep", "watch", "pause", "retire")

# Advisory recommendation thresholds. Conservative by design; they only shape the
# ``recommendation`` string and never the stored ``status``.
_MIN_JUDGED_FOR_RECOMMENDATION = 5
_TRUST_KEEP = 0.70
_TRUST_WATCH = 0.40
_TRUST_PAUSE = 0.20


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _as_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _as_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


@dataclass
class RoleRecord:
    """Per-role performance counters plus derived, advisory scores.

    ``cost_units`` is only populated when a real, attributed per-role cost is
    available. This module never invents or divides a shared LLM bill across
    roles; when the cost is unknown, ``cost_units`` stays ``0.0`` and
    ``cost_source`` stays ``"unknown"`` (TD-028 clarification 3).
    """

    role_id: str
    status: str = "active"
    invocations: int = 0
    successes: int = 0
    failures: int = 0
    vetoes: int = 0
    outputs_vetoed: int = 0
    proposals_created: int = 0
    proposals_approved: int = 0
    committed_local: int = 0
    rolled_back: int = 0
    cost_units: float = 0.0
    cost_source: str = "unknown"
    last_used_at: str | None = None
    trust_score: float = 0.0
    usefulness_score: float = 0.0
    recommendation: str = "keep"

    def to_dict(self) -> dict[str, Any]:
        return {
            "role_id": self.role_id,
            "status": self.status,
            "invocations": self.invocations,
            "successes": self.successes,
            "failures": self.failures,
            "vetoes": self.vetoes,
            "outputs_vetoed": self.outputs_vetoed,
            "proposals_created": self.proposals_created,
            "proposals_approved": self.proposals_approved,
            "committed_local": self.committed_local,
            "rolled_back": self.rolled_back,
            "cost_units": self.cost_units,
            "cost_source": self.cost_source,
            "last_used_at": self.last_used_at,
            "trust_score": self.trust_score,
            "usefulness_score": self.usefulness_score,
            "recommendation": self.recommendation,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RoleRecord":
        """Rebuild a record defensively; unknown/garbled fields fall back."""
        status = str(data.get("status") or "active")
        if status not in VALID_STATUSES:
            status = "active"
        return cls(
            role_id=str(data.get("role_id") or ""),
            status=status,
            invocations=_as_int(data.get("invocations")),
            successes=_as_int(data.get("successes")),
            failures=_as_int(data.get("failures")),
            vetoes=_as_int(data.get("vetoes")),
            outputs_vetoed=_as_int(data.get("outputs_vetoed")),
            proposals_created=_as_int(data.get("proposals_created")),
            proposals_approved=_as_int(data.get("proposals_approved")),
            committed_local=_as_int(data.get("committed_local")),
            rolled_back=_as_int(data.get("rolled_back")),
            cost_units=_as_float(data.get("cost_units")),
            cost_source=str(data.get("cost_source") or "unknown"),
            last_used_at=(str(data["last_used_at"]) if data.get("last_used_at") else None),
            trust_score=_as_float(data.get("trust_score")),
            usefulness_score=_as_float(data.get("usefulness_score")),
            recommendation=str(data.get("recommendation") or "keep"),
        )


def _trust_score(rec: RoleRecord) -> float:
    """Fraction of *judged* events that went well (0..1).

    Positives are successes plus Critic vetoes (a veto is the Critic doing its
    job). Negatives are failures plus Builder outputs that were vetoed. Neutral
    outcomes (e.g. manager ``no_target``) are ignored so they neither reward nor
    punish. Returns 0.0 when there is nothing judged yet.
    """
    positives = rec.successes + rec.vetoes
    negatives = rec.failures + rec.outputs_vetoed
    denom = positives + negatives
    if denom <= 0:
        return 0.0
    return round(positives / denom, 3)


def _usefulness_score(rec: RoleRecord) -> float:
    """Value delivered per invocation (0..1, capped).

    Value events: proposals created/approved, local commits, and Critic vetoes
    (catching a bad candidate is valuable). Rollbacks subtract. Normalised by
    invocations so a role that adds value on most runs trends toward 1.0.
    """
    if rec.invocations <= 0:
        return 0.0
    value = (
        rec.proposals_created
        + rec.proposals_approved
        + rec.committed_local
        + rec.vetoes
        - rec.rolled_back
    )
    if value <= 0:
        return 0.0
    return round(min(1.0, value / rec.invocations), 3)


def _recommendation(rec: RoleRecord) -> str:
    """Advisory only — NEVER changes ``status`` (TD-028 clarification 7).

    Below a minimum number of judged events there is not enough evidence, so we
    keep. Otherwise map trust to keep/watch/pause/retire. Because Critic vetoes
    count as positives in trust, a diligent Critic trends to ``keep``; a Builder
    with repeated failures or vetoed outputs trends toward watch/pause/retire.
    """
    judged = rec.successes + rec.vetoes + rec.failures + rec.outputs_vetoed
    if judged < _MIN_JUDGED_FOR_RECOMMENDATION:
        return "keep"
    trust = rec.trust_score
    if trust >= _TRUST_KEEP:
        return "keep"
    if trust >= _TRUST_WATCH:
        return "watch"
    if trust >= _TRUST_PAUSE:
        return "pause"
    return "retire"


def _recompute(rec: RoleRecord) -> None:
    rec.trust_score = _trust_score(rec)
    rec.usefulness_score = _usefulness_score(rec)
    rec.recommendation = _recommendation(rec)


# Per-role decision -> outcome bucket. ``None`` means "neutral" (counted as an
# invocation but neither success nor failure).
_DECISION_OUTCOME: dict[str, dict[str, str | None]] = {
    "manager": {"selected": "success", "no_target": None},
    "researcher": {"gathered": "success"},
    "builder": {"built": "success", "failed": "failure"},
    "critic": {"pass": "success", "veto": "veto"},
    "reporter": {"published": "success"},
}


class SubagentRegistry:
    """In-memory ledger of role performance, backed by a single JSON file.

    Load with :meth:`load`, feed it ``ProducerReport``-shaped results with
    :meth:`record_report`, and read a snapshot with :meth:`status_report`. All
    write helpers persist atomically and never raise on a corrupt/missing file.
    """

    def __init__(self, workspace: str | Path, roles: dict[str, RoleRecord] | None = None):
        self.workspace = Path(workspace)
        self.roles: dict[str, RoleRecord] = roles if roles is not None else {}
        self._ensure_defaults()

    # ── persistence ─────────────────────────────────────────────────────────

    @property
    def path(self) -> Path:
        return self.workspace / REGISTRY_PATH

    def _ensure_defaults(self) -> None:
        for role_id in DEFAULT_ROLES:
            if role_id not in self.roles:
                rec = RoleRecord(role_id=role_id)
                _recompute(rec)
                self.roles[role_id] = rec

    @classmethod
    def load(cls, workspace: str | Path) -> "SubagentRegistry":
        """Read the ledger; a missing/corrupt file degrades to default roles."""
        path = Path(workspace) / REGISTRY_PATH
        roles: dict[str, RoleRecord] = {}
        if path.exists():
            try:
                raw = json.loads(path.read_text(encoding="utf-8"))
                entries = raw.get("roles", {}) if isinstance(raw, dict) else {}
                if isinstance(entries, dict):
                    for role_id, data in entries.items():
                        if isinstance(data, dict):
                            rec = RoleRecord.from_dict({**data, "role_id": role_id})
                            _recompute(rec)
                            roles[role_id] = rec
            except (ValueError, OSError):
                roles = {}  # corrupt -> rebuild defaults
        return cls(workspace, roles)

    def save(self) -> None:
        """Persist atomically (tmp + replace). Best-effort; may raise on IO —
        callers wrap this so a write failure never breaks producer/daemon."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "updated_at": _now_iso(),
            "roles": {rid: rec.to_dict() for rid, rec in self.roles.items()},
        }
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8")
        tmp.replace(self.path)

    # ── recording ───────────────────────────────────────────────────────────

    def _role(self, role_id: str) -> RoleRecord:
        rec = self.roles.get(role_id)
        if rec is None:
            rec = RoleRecord(role_id=role_id)
            self.roles[role_id] = rec
        return rec

    def record_report(self, report: Any, *, save: bool = True) -> bool:
        """Record one producer run's role outcomes. Returns True if anything was
        recorded. Accepts a ``ProducerReport`` (or its ``to_dict()``) and only
        touches roles that actually ran, so gate-wait results (no roles) are a
        no-op. Never raises on shape surprises."""
        data = report.to_dict() if hasattr(report, "to_dict") else dict(report or {})
        role_outputs = data.get("roles") or data.get("role_outputs") or []
        if not role_outputs:
            return False

        now = _now_iso()
        touched: set[str] = set()
        builder_ran = False
        for ro in role_outputs:
            role_id = str(ro.get("role") or "").strip()
            if not role_id:
                continue
            decision = str(ro.get("decision") or "").strip()
            rec = self._role(role_id)
            rec.invocations += 1
            rec.last_used_at = now
            touched.add(role_id)
            if role_id == "builder":
                builder_ran = True
            outcome = _DECISION_OUTCOME.get(role_id, {}).get(decision, "unknown")
            if outcome == "success":
                rec.successes += 1
            elif outcome == "failure":
                rec.failures += 1
            elif outcome == "veto":
                rec.vetoes += 1  # useful veto: boosts trust/usefulness
            elif outcome is None:
                pass  # neutral (e.g. manager no_target)
            else:
                rec.failures += 1  # unexpected decision -> conservative failure

        status = str(data.get("status") or "")
        if status == "proposed":
            self._role("reporter").proposals_created += 1
            touched.add("reporter")
        elif status == "critic_veto" and builder_ran:
            # The Builder's output was rejected downstream; attribute it so
            # repeated vetoed outputs can move the Builder's *recommendation*.
            self._role("builder").outputs_vetoed += 1
            touched.add("builder")

        for role_id in touched:
            _recompute(self.roles[role_id])
        if save:
            self.save()
        return True

    def record_lane_outcome(
        self, role_id: str, outcome: str, *, save: bool = True
    ) -> bool:
        """Link a downstream self-apply lane outcome to a role (TD-023/TD-024).

        Defined for FUTURE wiring; this PR never invokes it automatically
        (TD-028 clarification 8). ``outcome`` is one of ``approved`` /
        ``committed_local`` / ``rolled_back``.
        """
        rec = self._role(role_id)
        if outcome == "approved":
            rec.proposals_approved += 1
        elif outcome == "committed_local":
            rec.committed_local += 1
        elif outcome == "rolled_back":
            rec.rolled_back += 1
        else:
            return False
        rec.last_used_at = _now_iso()
        _recompute(rec)
        if save:
            self.save()
        return True

    # ── reporting (read-only) ─────────────────────────────────────────────────

    def status_report(self) -> dict[str, Any]:
        """Read-only snapshot for operators. No LLM/provider/network/git."""
        roles = [self.roles[rid].to_dict() for rid in sorted(self.roles)]
        counts: dict[str, int] = {}
        for rec in self.roles.values():
            counts[rec.recommendation] = counts.get(rec.recommendation, 0) + 1
        return {
            "role_count": len(self.roles),
            "recommendation_counts": counts,
            "roles": roles,
        }

    def summary_line(self) -> str:
        """Compact one-line summary for the operator ``--status`` view."""
        counts: dict[str, int] = {}
        for rec in self.roles.values():
            counts[rec.recommendation] = counts.get(rec.recommendation, 0) + 1
        parts = [
            f"{rec}x{counts[rec]}"
            for rec in VALID_RECOMMENDATIONS
            if counts.get(rec)
        ]
        detail = " ".join(parts) if parts else "no data"
        return f"{len(self.roles)} roles - {detail}"
