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
from typing import Any, Mapping

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

# TD-031 canonical, narrow lane-outcome attribution. One role per outcome; do NOT
# credit all roles. ``reporter`` published the item that got approved; ``builder``
# owns the file content that either committed or had to be rolled back.
LANE_OUTCOME_ROLE: dict[str, str] = {
    "approved": "reporter",
    "committed_local": "builder",
    "rolled_back": "builder",
}

# TD-033 canonical human value-review attribution. Each verdict blames/credits
# exactly one role. ``accepted`` is confirmed value for the built change; each
# ``rejected_*`` points at the role most responsible for that failure mode.
VERDICT_ROLE: dict[str, str] = {
    "accepted": "builder",
    "rejected_low_value": "builder",
    "rejected_wrong_target": "manager",
    "rejected_misleading_summary": "reporter",
    "rejected_risky": "critic",
}

# Advisory recommendation thresholds. Conservative by design; they only shape the
# ``recommendation`` string and never the stored ``status``.
_MIN_JUDGED_FOR_RECOMMENDATION = 5
_TRUST_KEEP = 0.70
_TRUST_WATCH = 0.40
_TRUST_PAUSE = 0.20

# TD-031 amendment — technical success is NOT confirmed value.
#
# A self-apply lane outcome tells us the change was *technically* accepted:
#   - approved        : a human clicked approve in the inbox
#   - committed_local : the patch applied and the test suite passed locally
#   - rolled_back     : the lane reverted the change
# None of these prove the change was *worth making*. The live self-build
# experiment produced a ``committed_local`` proposal for core/redaction.py that
# passed tests yet was only a comment-capitalization edit dressed up as a
# "robustness improvement" — humans rejected it as low value. So technical
# outcomes get a small reliability nudge in usefulness, never a full value point.
#
# ``confirmed_value`` (a human-accepted / value-reviewed outcome, TD-033) is the
# separate, full-weight positive signal that a technical success actually
# delivered value; ``value_rejected`` is its full-weight negative. Both are owned
# exclusively by :meth:`SubagentRegistry.reconcile_value_reviews` (a projection of
# the TD-032 value-review ledger) — no other code path increments them.
_TECHNICAL_SUCCESS_WEIGHT = 0.25


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
    # TD-033 human value-review counters. Owned exclusively by
    # ``reconcile_value_reviews`` (projection of the value-review ledger); no
    # other method increments them, so they never double-count.
    confirmed_value: int = 0
    value_rejected: int = 0
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
            "confirmed_value": self.confirmed_value,
            "value_rejected": self.value_rejected,
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
            confirmed_value=_as_int(data.get("confirmed_value")),
            value_rejected=_as_int(data.get("value_rejected")),
            cost_units=_as_float(data.get("cost_units")),
            cost_source=str(data.get("cost_source") or "unknown"),
            last_used_at=(str(data["last_used_at"]) if data.get("last_used_at") else None),
            trust_score=_as_float(data.get("trust_score")),
            usefulness_score=_as_float(data.get("usefulness_score")),
            recommendation=str(data.get("recommendation") or "keep"),
        )


def _trust_score(rec: RoleRecord) -> float:
    """Fraction of *judged* events that went well (0..1).

    Positives are successes, Critic vetoes (a veto is the Critic doing its job)
    and human-``confirmed_value`` reviews. Negatives are failures, Builder
    outputs that were vetoed, and human ``value_rejected`` reviews. Neutral
    outcomes (e.g. manager ``no_target``) are ignored so they neither reward nor
    punish. Returns 0.0 when there is nothing judged yet.
    """
    positives = rec.successes + rec.vetoes + rec.confirmed_value
    negatives = rec.failures + rec.outputs_vetoed + rec.value_rejected
    denom = positives + negatives
    if denom <= 0:
        return 0.0
    return round(positives / denom, 3)


def _usefulness_score(rec: RoleRecord) -> float:
    """Value delivered per invocation (0..1, capped).

    Positive signals, deliberately weighted differently:

    * **Confirmed value** (full weight): human ``accepted`` value reviews
      (``confirmed_value``, TD-033). This is the only signal that a change was
      actually *worth making*, so it carries full weight.
    * **Producer-stage value** (full weight): proposals created and Critic
      vetoes. These reflect the pipeline doing its judgement work up front and
      do not depend on any downstream execution being *good*.
    * **Technical success** (``_TECHNICAL_SUCCESS_WEIGHT``, a small nudge):
      ``approved`` (``proposals_approved``) and ``committed_local``. These prove
      a change was technically accepted/applied, NOT that it was valuable — so
      they must not by themselves push a role to high usefulness. See the
      ``_TECHNICAL_SUCCESS_WEIGHT`` note for the redaction.py evidence.

    ``rolled_back`` and human ``value_rejected`` reviews both subtract at full
    weight (wasted work / a change humans judged not worth making). Normalised by
    invocations so a role that adds value on most runs trends toward 1.0.
    """
    if rec.invocations <= 0:
        return 0.0
    producer_stage_value = rec.proposals_created + rec.vetoes
    technical_success = rec.proposals_approved + rec.committed_local
    value = (
        rec.confirmed_value
        + producer_stage_value
        + _TECHNICAL_SUCCESS_WEIGHT * technical_success
        - rec.rolled_back
        - rec.value_rejected
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

    Human value reviews (``confirmed_value`` / ``value_rejected``, TD-033/034)
    count as judged evidence too, so that many human ``rejected_*`` verdicts can
    move the recommendation even when the role has little producer-stage volume.
    They already feed ``trust_score``; including them here lets the gate actually
    open on human signal. Technical-only outcomes (``proposals_approved`` /
    ``committed_local``) are deliberately NOT counted as judged evidence — they
    prove a change was applied, not that it was judged good or bad.
    """
    judged = (
        rec.successes
        + rec.vetoes
        + rec.failures
        + rec.outputs_vetoed
        + rec.confirmed_value
        + rec.value_rejected
    )
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

    def __init__(self, workspace: str | Path, roles: dict[str, RoleRecord] | None = None,
                 applied_outcomes: set[str] | None = None):
        self.workspace = Path(workspace)
        self.roles: dict[str, RoleRecord] = roles if roles is not None else {}
        # Persistent dedup set for TD-031 lane outcomes. Keyed
        # ``f"{item_id}:{role_id}:{outcome}"`` so re-firing a hook (or a restart)
        # never double-counts, and future multi-role crediting stays safe.
        self.applied_outcomes: set[str] = applied_outcomes if applied_outcomes is not None else set()
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
        applied: set[str] = set()
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
                # Tolerant-load: older ledgers have no ``applied_outcomes`` key.
                raw_applied = raw.get("applied_outcomes", []) if isinstance(raw, dict) else []
                if isinstance(raw_applied, list):
                    applied = {str(k) for k in raw_applied}
            except (ValueError, OSError):
                roles = {}  # corrupt -> rebuild defaults
                applied = set()
        return cls(workspace, roles, applied)

    def save(self) -> None:
        """Persist atomically (tmp + replace). Best-effort; may raise on IO —
        callers wrap this so a write failure never breaks producer/daemon."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "updated_at": _now_iso(),
            "roles": {rid: rec.to_dict() for rid, rec in self.roles.items()},
            "applied_outcomes": sorted(self.applied_outcomes),
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

        Low-level primitive. TD-031 invokes it via :meth:`apply_lane_outcome`
        (which adds correlation + persistent dedup) from guarded, best-effort
        hooks. ``outcome`` is one of ``approved`` / ``committed_local`` /
        ``rolled_back``. Only updates counters/scores/recommendation; it never
        mutates role ``status`` (advisory-only).
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

    def apply_lane_outcome(
        self, item_id: str, outcome: str, *, save: bool = True
    ) -> bool:
        """Record a lane/approval outcome for the approval item ``item_id`` (TD-031).

        Resolves the canonical crediting role via :data:`LANE_OUTCOME_ROLE`
        (``approved``->reporter, ``committed_local``/``rolled_back``->builder),
        then delegates to :meth:`record_lane_outcome`. Persistent dedup keyed
        ``f"{item_id}:{role_id}:{outcome}"`` makes it idempotent across re-fired
        hooks and restarts. Returns ``True`` only when it newly recorded. Never
        mutates role ``status``.
        """
        role_id = LANE_OUTCOME_ROLE.get(outcome)
        if role_id is None:
            return False
        key = f"{item_id}:{role_id}:{outcome}"
        if key in self.applied_outcomes:
            return False
        if not self.record_lane_outcome(role_id, outcome, save=False):
            return False
        self.applied_outcomes.add(key)
        if save:
            self.save()
        return True

    def reconcile_value_reviews(
        self, effective: Mapping[str, str], *, save: bool = True
    ) -> bool:
        """Project the TD-032 value-review ledger onto role scoring (TD-033).

        ``effective`` is ``{item_id: verdict}`` — the *latest valid* verdict per
        item, as produced by
        :meth:`core.value_review.ValueReviewLog.effective_by_item_id`.

        This is a **projection**, not an accumulation: it resets every role's
        ``confirmed_value`` / ``value_rejected`` to zero and rebuilds them from
        ``effective`` in full. That makes it naturally idempotent and immune to
        double-counting — a changed verdict (even one that moves attribution to a
        different role) simply re-projects. These two counters are owned solely
        by this method; no other code path increments them.

        Verdicts map to roles via :data:`VERDICT_ROLE`: ``accepted`` credits
        ``confirmed_value``; every ``rejected_*`` adds to that role's
        ``value_rejected``. Unknown verdicts are ignored. Only counters/scores/
        recommendation change — role ``status`` is never mutated.
        """
        for rec in self.roles.values():
            rec.confirmed_value = 0
            rec.value_rejected = 0
        for verdict in effective.values():
            role_id = VERDICT_ROLE.get(str(verdict))
            if role_id is None:
                continue
            rec = self._role(role_id)
            if verdict == "accepted":
                rec.confirmed_value += 1
            else:
                rec.value_rejected += 1
        for rec in self.roles.values():
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
