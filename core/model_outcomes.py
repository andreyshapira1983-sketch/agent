"""Which model on which role actually gives verified answers."""
from __future__ import annotations

import json
from collections.abc import Container, Iterable, Mapping
from dataclasses import dataclass
from pathlib import Path

from core.model_catalog import classify_model, offered_models, peer_model_at_same_tier

#: Below this many runs a share is noise, not evidence.
MIN_RUNS = 8


@dataclass(frozen=True)
class ModelOutcome:
    """Outcome of one model on one role."""

    role: str
    provider: str
    model: str
    runs: int
    fully_verified: int
    with_defect: int

    @property
    def verified_share(self) -> float:
        return self.fully_verified / self.runs if self.runs else 0.0

    @property
    def defect_share(self) -> float:
        return self.with_defect / self.runs if self.runs else 0.0

    def to_dict(self) -> dict[str, object]:
        return {
            "role": self.role,
            "provider": self.provider,
            "model": self.model,
            "runs": self.runs,
            "verified_share": round(self.verified_share, 3),
            "defect_share": round(self.defect_share, 3),
        }


def _episode_verdict(episode: Mapping[str, object]) -> tuple[bool, bool]:
    """(fully verified, detectors ran) for one episode."""
    verified = int(episode.get("verified_chunks") or 0)
    unverified = int(episode.get("unverified_chunks") or 0)
    return (verified > 0 and unverified == 0), bool(episode.get("defect_signals"))


def measure_model_outcomes(
    usage: Iterable[Mapping[str, object]],
    episodes: Iterable[Mapping[str, object]],
) -> tuple[ModelOutcome, ...]:
    """Join model calls with the outcomes of their runs. Pure function."""
    by_run: dict[str, Mapping[str, object]] = {}
    for episode in episodes:
        run_id = str(episode.get("run_id") or "")
        if run_id:
            by_run[run_id] = episode

    seen: set[tuple[str, str, str, str]] = set()
    tally: dict[tuple[str, str, str], list[int]] = {}
    for call in usage:
        if str(call.get("status") or "") != "success":
            continue
        episode = by_run.get(str(call.get("run_id") or ""))
        if episode is None:
            continue
        key = (
            str(call.get("role") or ""),
            str(call.get("provider") or ""),
            str(call.get("model") or ""),
        )
        run_key = (*key, str(call.get("run_id") or ""))
        if run_key in seen:
            continue
        seen.add(run_key)
        verified, defect = _episode_verdict(episode)
        row = tally.setdefault(key, [0, 0, 0])
        row[0] += 1
        row[1] += int(verified)
        row[2] += int(defect)

    return tuple(
        ModelOutcome(role, provider, model, runs, ver, defect)
        for (role, provider, model), (runs, ver, defect) in sorted(tally.items())
    )


#: How close verified shares must be to count as indistinguishable: below one error step at MIN_RUNS, so equality means exact equality.
_COST_TOLERANCE = 0.05


#: Bridge between the classifier vocabulary (light/standard/deep) and the price table (low/medium/high); a test keeps it complete.
_TIER_TO_COST: dict[str, str] = {
    "light": "low",
    "standard": "medium",
    "deep": "high",
}


def _cost_units(model: str) -> int:
    """Tariff of the model's tier, from the single price table."""
    from core.model_usage import cost_units_per_1k

    return cost_units_per_1k(_TIER_TO_COST.get(classify_model(model).value, "unknown"))


def preferred_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    min_runs: int = MIN_RUNS,
    offered: Container[str] | None = None,
) -> str | None:
    """Best MEASURED model for this role at this provider, or None."""
    ranked = [
        outcome for outcome in outcomes
        if outcome.role == role and outcome.provider == provider
        and outcome.runs >= min_runs
        and (offered is None or outcome.model in offered)
    ]
    if not ranked:
        return None
    # cost axis: among models indistinguishable in quality from the best, the cheaper tariff wins; the tolerance is below one error step (1/8) so measurably worse is never bought
    best_share = max(o.verified_share for o in ranked)
    bar = [o for o in ranked if best_share - o.verified_share <= _COST_TOLERANCE]
    bar.sort(key=lambda o: (
        _cost_units(o.model), -o.verified_share, o.defect_share, -o.runs,
    ))
    return bar[0].model


#: Every fourth table run hands the next decision to the scout so an unmeasured peer reaches MIN_RUNS.
SCOUT_PERIOD = 4


def scout_turn(decisions: int, *, period: int = SCOUT_PERIOD) -> bool:
    """Whose turn: scout or winner, by a MONOTONIC decision count."""
    return decisions % period == 0


def failover_decisions(
    workspace: Path | None = None, *, role: str, provider: str,
) -> int:
    """How many failover decisions a role+provider pair has had; monotonic.

    One run is one decision however many calls the substitute made; rows without run_id count individually.
    """
    root = Path(workspace or ".")
    runs: set[str] = set()
    loose = 0
    for row in _load_rows(root / "data" / "model_usage.jsonl"):
        if (str(row.get("role") or "") != role
                or str(row.get("provider") or "") != provider):
            continue
        if not str(row.get("route_reason") or "").startswith("provider_failover"):
            continue
        rid = str(row.get("run_id") or "")
        if rid:
            runs.add(rid)
        else:
            loose += 1
    return len(runs) + loose


def scout_model(
    outcomes: Iterable[ModelOutcome],
    *,
    role: str,
    provider: str,
    current_model: str | None,
    min_runs: int = MIN_RUNS,
) -> str | None:
    """An unmeasured peer at the same tier: a scouting candidate, or None."""
    peer = peer_model_at_same_tier(current_model, provider)
    if not peer:
        return None
    for outcome in outcomes:
        if (outcome.role == role and outcome.provider == provider
                and outcome.model == peer and outcome.runs >= min_runs):
            return None
    return peer


# Hook into replacement choice: the measured preference comes FIRST, the name tier map is the floor when there is nothing to measure.

#: Outcomes read per process, reset by the sources' modification time.
_CACHE: dict[str, object] = {}


def _load_rows(path: Path) -> list[dict]:
    out: list[dict] = []
    if not path.is_file():
        return out
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        out.append(row.get("payload") or row)
    return out


def measured_outcomes(workspace: Path | None = None) -> tuple[ModelOutcome, ...]:
    """Outcomes from the workspace data, cached by file modification time."""
    root = Path(workspace or ".")
    usage_path = root / "data" / "model_usage.jsonl"
    episodes_path = root / "data" / "episodic_memory.jsonl"
    stamp = tuple(
        p.stat().st_mtime_ns if p.is_file() else 0
        for p in (usage_path, episodes_path)
    )
    if _CACHE.get("stamp") == stamp:
        return _CACHE["outcomes"]  # type: ignore[return-value]
    outcomes = measure_model_outcomes(
        _load_rows(usage_path), _load_rows(episodes_path)
    )
    _CACHE["stamp"] = stamp
    _CACHE["outcomes"] = outcomes
    return outcomes


def substitute_model(
    *, role: str, provider: str, current_model: str | None,
    workspace: Path | None = None,
) -> str | None:
    """Which model replaces `current_model` at the new provider."""
    return substitute_model_with_reason(
        role=role, provider=provider, current_model=current_model,
        workspace=workspace,
    )[0]


def substitute_model_with_reason(
    *, role: str, provider: str, current_model: str | None,
    workspace: Path | None = None,
) -> tuple[str | None, str]:
    """(model, reason): the decision must explain itself."""
    try:
        outcomes = measured_outcomes(workspace)
        measured = preferred_model(
            outcomes, role=role, provider=provider,
            offered=offered_models(provider) or None,
        )
    except OSError:
        outcomes, measured = (), None
    material = sum(
        o.runs for o in outcomes
        if o.role == role and o.provider == provider
    )
    if measured:
        scout = scout_model(
            outcomes, role=role, provider=provider, current_model=current_model,
        )
        decisions = failover_decisions(workspace, role=role, provider=provider)
        if scout and scout != measured and scout_turn(decisions):
            return scout, f"scout:{scout}(mat={material},dec={decisions})"
        why = (
            "off-turn" if scout and scout != measured
            else ("no-scout-candidate" if not scout else "scout==measured")
        )
        return measured, f"measured:{measured}(mat={material},dec={decisions},{why})"
    peer = peer_model_at_same_tier(current_model, provider)
    return peer, f"floor:{peer}(mat={material})"
