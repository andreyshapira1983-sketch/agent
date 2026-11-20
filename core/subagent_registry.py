"""Subagent role performance ledger.

    A small persistent registry recording how the self-build producer's roles
    (Manager, Researcher, Builder, Critic, Reporter) perform over time. It only
    RECORDS and REPORTS: ``recommendation`` (keep / watch / pause / retire) is
    advisory and never changes a role's stored ``status``. No LLM, network or git
    work; one JSON file under ``data/``. Writes are best-effort (callers wrap
    ``record_*``). Decisions: manager selected / researcher gathered / builder
    built / critic pass / reporter published = success; manager no_target =
    neutral; builder failed = failure; critic veto = useful veto (raises Critic
    trust) and marks ``outputs_vetoed`` on the Builder, which can move only its
    recommendation."""
from __future__ import annotations

import json
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from core.subagent_contract import CanonicalSubagentContract
from core.subagent_contract_audit import ContractAuditReport, SubagentExecutionReceipt

# Persistent ledger location (gitignored ``data/``): one small JSON file keyed by role id; atomic tmp+replace on write.
REGISTRY_PATH = "data/subagent_registry.json"

# The producer's role-level subagents, treated as ROLES, not independent persistent agents.
DEFAULT_ROLES: tuple[str, ...] = (
    "manager",
    "researcher",
    "builder",
    "critic",
    "reporter",
)

VALID_STATUSES: tuple[str, ...] = ("active", "paused", "retired")
VALID_RECOMMENDATIONS: tuple[str, ...] = ("keep", "watch", "pause", "retire")

# Narrow lane-outcome attribution: one role per outcome, never all. ``reporter`` published the approved item; ``builder`` owns the file content that committed or was rolled back.
LANE_OUTCOME_ROLE: dict[str, str] = {
    "approved": "reporter",
    "committed_local": "builder",
    "rolled_back": "builder",
}

# Human value-review attribution: each verdict credits/blames exactly one role. ``accepted`` is confirmed value; each ``rejected_*`` points at the most responsible role.
VERDICT_ROLE: dict[str, str] = {
    "accepted": "builder",
    "rejected_low_value": "builder",
    "rejected_wrong_target": "manager",
    "rejected_misleading_summary": "reporter",
    "rejected_risky": "critic",
}

# Advisory recommendation thresholds, conservative; they shape only the ``recommendation`` string.
_MIN_JUDGED_FOR_RECOMMENDATION = 5
_TRUST_KEEP = 0.70
_TRUST_WATCH = 0.40
_TRUST_PAUSE = 0.20

# Technical success is NOT confirmed value. Lane outcomes (approved, committed_local, rolled_back) show a change was technically accepted, not that it was worth making, so they give only a small reliability nudge to usefulness. ``confirmed_value`` and ``value_rejected`` (human value reviews) are the full-weight signals, owned exclusively by :meth:`SubagentRegistry.reconcile_value_reviews`.
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
    """Per-role performance counters plus derived, advisory scores."""

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
    # Human value-review counters, owned exclusively by ``reconcile_value_reviews`` so they never double-count.
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
    def from_dict(cls, data: dict[str, Any]) -> RoleRecord:
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


@dataclass
class ContractRunRecord:
    """Version-separated runtime counters for one canonical contract.

        Independent from :class:`RoleRecord` so verified value is reconciled exactly once and an execution is not double-counted as role reputation."""

    contract_id: str
    schema_version: int
    role_id: str
    source: str
    invocations: int = 0
    executed: int = 0
    errors: int = 0
    refused: int = 0
    audit_passes: int = 0
    audit_failures: int = 0
    audit_unknown: int = 0
    last_execution_receipt: dict[str, Any] | None = None
    last_audit_report: dict[str, Any] | None = None
    last_outcome: str | None = None
    last_used_at: str | None = None

    @property
    def key(self) -> str:
        return _contract_run_key(self.contract_id, self.schema_version)

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_id": self.contract_id,
            "schema_version": self.schema_version,
            "role_id": self.role_id,
            "source": self.source,
            "invocations": self.invocations,
            "executed": self.executed,
            "errors": self.errors,
            "refused": self.refused,
            "audit_passes": self.audit_passes,
            "audit_failures": self.audit_failures,
            "audit_unknown": self.audit_unknown,
            "last_execution_receipt": self.last_execution_receipt,
            "last_audit_report": self.last_audit_report,
            "last_outcome": self.last_outcome,
            "last_used_at": self.last_used_at,
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ContractRunRecord | None:
        contract_id = str(data.get("contract_id") or "").strip()
        role_id = str(data.get("role_id") or "").strip()
        schema_version = _as_int(data.get("schema_version"), default=0)
        if not contract_id or not role_id or schema_version < 1:
            return None
        last_outcome = str(data.get("last_outcome") or "") or None
        if last_outcome not in {None, "executed", "error", "refused"}:
            last_outcome = None
        return cls(
            contract_id=contract_id,
            schema_version=schema_version,
            role_id=role_id,
            source=str(data.get("source") or "unknown"),
            invocations=_as_int(data.get("invocations")),
            executed=_as_int(data.get("executed")),
            errors=_as_int(data.get("errors")),
            refused=_as_int(data.get("refused")),
            audit_passes=_as_int(data.get("audit_passes")),
            audit_failures=_as_int(data.get("audit_failures")),
            audit_unknown=_as_int(data.get("audit_unknown")),
            last_execution_receipt=(
                dict(data["last_execution_receipt"])
                if isinstance(data.get("last_execution_receipt"), dict)
                else None
            ),
            last_audit_report=(
                dict(data["last_audit_report"])
                if isinstance(data.get("last_audit_report"), dict)
                else None
            ),
            last_outcome=last_outcome,
            last_used_at=(str(data["last_used_at"]) if data.get("last_used_at") else None),
        )


def _contract_run_key(contract_id: str, schema_version: int) -> str:
    return f"{contract_id}@v{schema_version}"


def _trust_score(rec: RoleRecord) -> float:
    """Fraction of *judged* events that went well (0..1)."""
    positives = rec.successes + rec.vetoes + rec.confirmed_value
    negatives = rec.failures + rec.outputs_vetoed + rec.value_rejected
    denom = positives + negatives
    if denom <= 0:
        return 0.0
    return round(positives / denom, 3)


def _usefulness_score(rec: RoleRecord) -> float:
    """Value delivered per invocation (0..1, capped).

        Full weight: human-confirmed value, and producer-stage value (proposals created, Critic vetoes). Small nudge (``_TECHNICAL_SUCCESS_WEIGHT``): ``approved`` and ``committed_local``. ``rolled_back`` and human ``value_rejected`` subtract at full weight. Normalised by invocations."""
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
    """Advisory only: NEVER changes ``status``."""
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


# Per-role decision -> outcome bucket; ``None`` is neutral (an invocation, neither success nor failure).
_DECISION_OUTCOME: dict[str, dict[str, str | None]] = {
    "manager": {"selected": "success", "no_target": None},
    "researcher": {"gathered": "success"},
    "builder": {"built": "success", "failed": "failure"},
    "critic": {"pass": "success", "veto": "veto"},
    "reporter": {"published": "success"},
}


class SubagentRegistry:
    """In-memory ledger of role performance, backed by a single JSON file.

        Load with :meth:`load`, feed ``ProducerReport``-shaped results to :meth:`record_report`, read a snapshot with :meth:`status_report`. Writes are atomic and never raise on a corrupt/missing file."""

    def __init__(
        self,
        workspace: str | Path,
        roles: dict[str, RoleRecord] | None = None,
        applied_outcomes: set[str] | None = None,
        contract_runs: dict[str, ContractRunRecord] | None = None,
    ):
        self.workspace = Path(workspace)
        self.roles: dict[str, RoleRecord] = roles if roles is not None else {}
        # Persistent dedup set for lane outcomes, keyed ``f"{item_id}:{role_id}:{outcome}"``, so a re-fired hook or restart never double-counts.
        self.applied_outcomes: set[str] = applied_outcomes if applied_outcomes is not None else set()
        self.contract_runs: dict[str, ContractRunRecord] = (
            contract_runs if contract_runs is not None else {}
        )
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
    def load(cls, workspace: str | Path) -> SubagentRegistry:
        """Read the ledger; a missing/corrupt file degrades to default roles."""
        path = Path(workspace) / REGISTRY_PATH
        roles: dict[str, RoleRecord] = {}
        applied: set[str] = set()
        contract_runs: dict[str, ContractRunRecord] = {}
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
                # Tolerant load: older ledgers have no ``applied_outcomes`` key.
                raw_applied = raw.get("applied_outcomes", []) if isinstance(raw, dict) else []
                if isinstance(raw_applied, list):
                    applied = {str(k) for k in raw_applied}
                raw_contracts = (
                    raw.get("contract_runs", {}) if isinstance(raw, dict) else {}
                )
                if isinstance(raw_contracts, dict):
                    for data in raw_contracts.values():
                        if not isinstance(data, dict):
                            continue
                        record = ContractRunRecord.from_dict(data)
                        if record is not None:
                            contract_runs[record.key] = record
            except (ValueError, OSError):
                roles = {}  # corrupt -> rebuild defaults
                applied = set()
                contract_runs = {}
        return cls(workspace, roles, applied, contract_runs)

    def save(self) -> None:
        """Persist atomically (tmp + replace). Best-effort; may raise on IO, callers wrap it."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        record = {
            "updated_at": _now_iso(),
            "roles": {rid: rec.to_dict() for rid, rec in self.roles.items()},
            "applied_outcomes": sorted(self.applied_outcomes),
            "contract_runs": {
                key: rec.to_dict() for key, rec in sorted(self.contract_runs.items())
            },
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

    def record_contract_run(
        self,
        contract: CanonicalSubagentContract,
        outcome: str,
        *,
        execution_receipt: SubagentExecutionReceipt | None = None,
        audit_report: ContractAuditReport | None = None,
        save: bool = True,
    ) -> bool:
        """Record one canonical runtime outcome without touching role scores."""
        if outcome not in {"executed", "error", "refused"}:
            return False
        if audit_report is not None and (
            audit_report.contract_id != contract.contract_id
            or audit_report.schema_version != contract.schema_version
        ):
            raise ValueError("audit report identity does not match canonical contract")
        if execution_receipt is not None and (
            execution_receipt.contract_id != contract.contract_id
            or execution_receipt.schema_version != contract.schema_version
        ):
            raise ValueError("execution receipt identity does not match canonical contract")
        key = _contract_run_key(contract.contract_id, contract.schema_version)
        record = self.contract_runs.get(key)
        if record is None:
            record = ContractRunRecord(
                contract_id=contract.contract_id,
                schema_version=contract.schema_version,
                role_id=contract.role,
                source=contract.source,
            )
            self.contract_runs[key] = record
        elif record.role_id != contract.role or record.source != contract.source:
            raise ValueError(f"canonical contract identity collision: {key}")

        record.invocations += 1
        if outcome == "executed":
            record.executed += 1
        elif outcome == "error":
            record.errors += 1
        else:
            record.refused += 1
        if audit_report is not None:
            if audit_report.verdict == "pass":
                record.audit_passes += 1
            elif audit_report.verdict == "fail":
                record.audit_failures += 1
            else:
                record.audit_unknown += 1
            record.last_audit_report = audit_report.to_dict()
        if execution_receipt is not None:
            record.last_execution_receipt = execution_receipt.to_dict()
        record.last_outcome = outcome
        record.last_used_at = _now_iso()
        if save:
            self.save()
        return True

    def record_report(self, report: Any, *, save: bool = True) -> bool:
        """Record one producer run's role outcomes. Returns True if anything was recorded.

            Accepts a ``ProducerReport`` (or its ``to_dict()``) and touches only roles that ran; gate-wait results are a no-op. Never raises on shape surprises."""
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
            # The Builder's output was rejected downstream; attribute it so repeated vetoes can move the Builder's recommendation.
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
        """Link a downstream self-apply lane outcome to a role."""
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
        """Record a lane/approval outcome for the approval item ``item_id``.

            Resolves the crediting role via :data:`LANE_OUTCOME_ROLE` (``approved``->reporter, ``committed_local``/``rolled_back``->builder) and delegates to :meth:`record_lane_outcome`. Idempotent through the persistent dedup key. Returns ``True`` only when newly recorded. Never mutates ``status``."""
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
        """Project the value-review ledger onto role scoring.

            Verdicts map to roles via :data:`VERDICT_ROLE`: ``accepted`` credits ``confirmed_value``, every ``rejected_*`` adds to that role's ``value_rejected``; unknown verdicts are ignored. Only counters, scores and recommendation change."""
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
            "contract_count": len(self.contract_runs),
            "contract_runs": [
                self.contract_runs[key].to_dict()
                for key in sorted(self.contract_runs)
            ],
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
