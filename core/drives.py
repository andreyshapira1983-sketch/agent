"""Internal drives: physiology around the model, computed from on-disk logs (measurement only).

Each drive is a number 0..1 with an explanation, derived from the campaign log, drive decisions, approval inbox and observation ladder. Interest is driven by events and progress, not hours: an area's interest is absolute learning progress (change in success share between recent and previous attempts; MAGELLAN, Oudeyer); mastered or never-succeeding areas give none, barely tried ones a small constant "try it" interest. Self-improvement counts tasked defects and proven edits, weighted by whether repairs succeed. Only `idle_time` still depends on hours.

    python -m core.drives            # current drives of this workspace
"""
from __future__ import annotations

import json
import math
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

#: Knowledge areas and an episode's mark in one are the FULL folder path: "cs/txt" sits inside "physics/txt", and a substring credited physics to computer science.
DOMAINS: dict[str, tuple[str, ...]] = {
    "math": ("math_study/",),
    "physics": ("knowledge_library/physics/",),
    "cs": ("knowledge_library/cs/",),
}

#: Hours for the idle signal to rise to ~0.63; the only clock among the drives (idle is a signal, not content: `_NOT_CONTENT`).
TAU_HOURS = {"idle_time": 0.5}
#: Progress is judged over this many last attempts of a drive (halved: was/became).
LP_WINDOW = 8
#: Fewer attempts: progress not visible; interest = "try it".
LP_MIN_ATTEMPTS = 4
#: Interest in a barely tried area; decays per attempt.
EXPLORE_VALUE = 0.3
#: An attempt older than this many days is forgotten: a long-abandoned area is new again.
ATTEMPT_HORIZON_DAYS = 7
_WORK_RESULTS = frozenset({"completed"})
#: A breakdown: action taken but no result. Idle ("nothing to do") and a skipped repeat are not breakdowns: that is `idle_time`, or one fact would count twice.
_BROKEN_RESULTS = frozenset({"failed", "empty"})
_WORD = re.compile(r"[a-zа-яё0-9_]{3,}", re.IGNORECASE)


def _rows(path: Path) -> list[dict[str, Any]]:
    """jsonl rows; the integrity wrapper (`payload`) is removed."""
    out: list[dict[str, Any]] = []
    try:
        text = path.read_text(encoding="utf-8")
    except OSError:
        return out
    for line in text.splitlines():
        try:
            row = json.loads(line)
        except ValueError:
            continue
        payload = row.get("payload") if isinstance(row.get("payload"), dict) else row
        if isinstance(payload, dict):
            out.append(payload)
    return out


def _ts(value: Any) -> datetime | None:
    try:
        t = datetime.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _growth(since: datetime | None, now: datetime, tau: float) -> float:
    if since is None:
        return 1.0
    hours = max(0.0, (now - since).total_seconds() / 3600)
    return 1.0 - math.exp(-hours / tau)


def _ago(since: datetime | None, now: datetime) -> str:
    if since is None:
        return "ни разу"
    minutes = int((now - since).total_seconds() // 60)
    return f"{minutes} мин назад" if minutes < 120 else f"{minutes // 60} ч назад"


def _similarity(goals: list[str]) -> float:
    """Mean pairwise similarity of recent goals (word Jaccard)."""
    sets = [set(_WORD.findall(g.lower())) for g in goals if g]
    sets = [s for s in sets if s]
    pairs = [(a, b) for i, a in enumerate(sets) for b in sets[i + 1:]]
    if not pairs:
        return 0.0
    return sum(len(a & b) / len(a | b) for a, b in pairs) / len(pairs)


#: Requests for the run's own rights: only a human can move them.
_HUMAN_ONLY_OPERATIONS = frozenset({"autonomous_runtime.allow_effects", "autonomous_runtime.standing_grant"})


def open_obligations(root: Path) -> list[dict[str, Any]]:
    """Pending requests the agent itself can move; a thing it cannot move is not its unfinished business."""
    from core.self_build_rules import blocking_lesson

    out = []
    for r in _rows(root / "data" / "approval_inbox.jsonl"):
        if r.get("status") != "pending" or r.get("operation") in _HUMAN_ONLY_OPERATIONS:
            continue
        files = (r.get("payload") or {}).get("files") or []
        targets = [f.get("path") if isinstance(f, dict) else f for f in files]
        if targets and blocking_lesson(root, targets) is not None:
            continue
        out.append(r)
    return out


#: Material for "improve self": an own module with proof of an edit that nobody is waiting on in the inbox and that has no rollback lesson.
_OWN_CODE_DIRS = ("core", "tools")
_SELF_CHANGE_OPS = frozenset({"self_apply_lane.run"})


def self_improvement_targets(root: Path, limit: int = 5) -> list[tuple[str, int]]:
    """Own modules with a proven edit proposable RIGHT NOW, strongest first (see `self_improvement_proofs`)."""
    return [(rel, lines) for rel, lines, _proof in self_improvement_proofs(root, limit)]


def _paused_targets(root: Path) -> frozenset[str]:
    """Files paused for the hands: recently denied by a human or refused by the splitter unchanged."""
    # Read the inbox as raw rows (`_rows`), not via ApprovalInbox, which "repairs" rows without the integrity wrapper on open; a reader must not rewrite.
    from core.approval_inbox import _DENIAL_MEMORY_HOURS, _payload_targets, _within_hours
    from core.self_apply_bridge import SELF_APPLY_OPERATION
    from core.splitter_refusals import refused_unchanged

    denied: set[str] = set()
    for row in _rows(root / "data" / "approval_inbox.jsonl"):
        if (row.get("status") == "denied" and row.get("operation") == SELF_APPLY_OPERATION
                and _within_hours(row.get("updated_at"), _DENIAL_MEMORY_HOURS)):
            denied.update(_payload_targets(row.get("payload") or {}))
    return frozenset(denied) | refused_unchanged(root)


def self_improvement_proofs(root: Path, limit: int = 5) -> list[tuple[str, int, Any]]:
    """Own modules with PROOF they should be reworked, and the proof.

    Size is not proof: proofs come from `core/split_proof.py`, ordered by kind, weight, then size. Modules already waiting for a human or closed by a rollback lesson are skipped, and CRITICAL_DENY modules are excluded by the proposal producer's own rule.
    """
    from core.self_build_producer import _is_critical
    from core.self_build_rules import blocking_lesson
    from core.split_proof import index_workspace, proof_for, rank_key

    # The producer stays silent while an UNDECIDED lane proposal sits in the inbox (one at a time, `_has_pending_self_build_proposal`), so no module is offered meanwhile.
    if any(r.get("operation") in _SELF_CHANGE_OPS and r.get("status") == "pending"
           for r in _rows(root / "data" / "approval_inbox.jsonl")):
        return []
    index = index_workspace(root)
    paused = _paused_targets(root)
    out: list[tuple[str, int, Any]] = []
    for rel, mod in index.items():
        if not rel.startswith(tuple(f"{d}/" for d in _OWN_CODE_DIRS)):
            continue
        if _is_critical(rel) or rel in paused or blocking_lesson(root, [rel]) is not None:
            continue
        proof = proof_for(rel, index)
        if proof is not None:
            out.append((rel, mod.lines, proof))
    out.sort(key=lambda item: rank_key(item[2], item[1]))
    return out[:limit]


def _tasked_defect_count(root: Path) -> int:
    """Open registry defects that can be repaired: a task is named (core/patch_route.py skips defects without one)."""
    try:
        from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

        return sum(1 for i in SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH).unresolved()
                   if str(i.suggested_next_action or "").strip())
    except Exception:  # noqa: BLE001 — an unreadable registry = zero reasons, the rest is computed
        return 0


def drive_outcomes(root: Path, now: datetime) -> dict[str, list[bool]]:
    """Goal outcomes per drive, in order: True = the campaign judge accepted the goal.

    A drive decision (data/drive_decisions.jsonl) is matched to the first outcome of the same goal in the campaign log; attempts older than ATTEMPT_HORIZON_DAYS or later than `now` are ignored.
    """
    ledger: dict[str, list[tuple[datetime, str]]] = {}
    for r in _rows(root / "data" / "campaign_ledger.jsonl"):
        t = _ts(r.get("ts"))
        if t is not None and r.get("goal") and r.get("result") in _WORK_RESULTS | _BROKEN_RESULTS:
            ledger.setdefault(str(r["goal"]), []).append((t, str(r["result"])))
    horizon = now - timedelta(days=ATTEMPT_HORIZON_DAYS)
    out: dict[str, list[bool]] = {}
    for d in _rows(root / "data" / "drive_decisions.jsonl"):
        t = _ts(d.get("ts"))
        if (d.get("status") != "proposed" or not d.get("drive") or not d.get("goal")
                or t is None or not horizon <= t <= now):
            continue
        result = next((res for rt, res in ledger.get(str(d["goal"]), []) if t <= rt <= now), None)
        if result is not None:
            out.setdefault(str(d["drive"]), []).append(result in _WORK_RESULTS)
    return out


#: Event drives: the reason is a fact on disk, and the outcome of their goals judges their weight.
_EVENT_DRIVES = ("stuck_need", "maintenance_need", "uncertainty", "unfinished_obligations",
                 "novelty_need")


def _learnable(outcomes: list[bool]) -> float:
    """Whether the drive's goal is learnable: Laplace success share; 1.0 below LP_MIN_ATTEMPTS (no history, weight untouched)."""
    if len(outcomes) < LP_MIN_ATTEMPTS:
        return 1.0
    return (sum(outcomes) + 1) / (len(outcomes) + 2)


def learning_interest(outcomes: list[bool]) -> tuple[float, str, str]:
    """Interest in an area by progress: (value, mode, explanation).

    Absolute learning progress over the last LP_WINDOW attempts; mastered and never-succeeding areas give zero; with fewer than LP_MIN_ATTEMPTS a small "try it" interest that decays per attempt.
    """
    recent = outcomes[-LP_WINDOW:]
    n = len(recent)
    if n < LP_MIN_ATTEMPTS:
        return (EXPLORE_VALUE * (1 - n / LP_MIN_ATTEMPTS), "explore",
                f"недавних попыток: {n} — область почти не пробована")
    half = n // 2
    early, late = recent[:half], recent[half:]
    progress = abs(sum(late) / len(late) - sum(early) / len(early))
    return (progress, "progress",
            (f"удачи в последних попытках: было {sum(early)} из {len(early)}, "
             f"стало {sum(late)} из {len(late)}"))


def compute_drives(workspace: Path | str, now: datetime | None = None) -> dict[str, dict[str, Any]]:
    """All drives: {name: {"value": 0..1, "why": str}}. Reads disk only."""
    root = Path(workspace)
    now = now or datetime.now(timezone.utc)
    ledger = _rows(root / "data" / "campaign_ledger.jsonl")
    drives: dict[str, dict[str, Any]] = {}

    last_work = max((t for r in ledger if r.get("result") in _WORK_RESULTS
                     for t in [_ts(r.get("ts"))] if t), default=None)
    drives["idle_time"] = {"value": _growth(last_work, now, TAU_HOURS["idle_time"]),
                           "why": f"последний полезный цикл: {_ago(last_work, now)}"}

    outcomes = drive_outcomes(root, now)
    for domain in [*DOMAINS, "world"]:
        value, mode, why = learning_interest(outcomes.get(f"competence_{domain}", []))
        drives[f"competence_{domain}"] = {"value": value, "mode": mode, "why": why}

    recent_goals: list[str] = []
    for r in reversed(ledger):
        g = str(r.get("goal") or "")
        if g and g not in recent_goals:
            recent_goals.append(g)
        if len(recent_goals) >= 8:
            break
    sim = _similarity(recent_goals)
    drives["novelty_need"] = {"value": sim,
                              "why": f"схожесть последних {len(recent_goals)} целей между собой: {sim:.2f}"}

    pending = open_obligations(root)
    drives["unfinished_obligations"] = {"value": 1.0 - math.exp(-len(pending) / 5),
                                        "why": f"незавершённых дел, которые можно сдвинуть: {len(pending)}"}

    targets = self_improvement_proofs(root)
    # The reason to repair itself is an event (a tasked defect or a proven edit), not time since the last edit, and is trusted as far as repairs succeed.
    tasked = _tasked_defect_count(root)
    material = tasked + len(targets)
    repairs = outcomes.get("self_improvement_need", [])[-LP_WINDOW:]
    learnable = _learnable(repairs)
    drives["self_improvement_need"] = {
        "value": (1.0 - math.exp(-material / 3)) * learnable if material else 0.0,
        "why": (f"открытых дефектов в реестре с задачей: {tasked}"
                + (f"; доказано: {targets[0][2].describe(targets[0][0])}" if targets else "")
                + f"; удач в последних починках: {sum(repairs)} из {len(repairs)}"
                if material else
                "ни доказательства правки, ни дефекта с задачей — "
                "или всё занято ящиком или уроками"),
    }

    tail = ledger[-20:]
    acted = [r for r in tail if r.get("result") in _BROKEN_RESULTS | _WORK_RESULTS]
    broken = sum(1 for r in acted if r.get("result") in _BROKEN_RESULTS)
    drives["maintenance_need"] = {"value": broken / len(acted) if acted else 0.0,
                                  "why": f"из последних {len(acted)} попыток действия сломались: {broken}"}

    try:
        from core.causal_climb_action import unexplained_observations

        unexplained = len(unexplained_observations(root))
    except Exception:  # noqa: BLE001 — no ladder, no such value; the rest is computed
        unexplained = 0
    drives["uncertainty"] = {"value": 1.0 - math.exp(-unexplained / 5),
                             "why": f"необъяснённых наблюдений: {unexplained}"}

    # Stuck: ask (internet and a partner); a hint alone was not a mechanism.
    from core.stuck_route import stuck_evidence

    stuck = len(stuck_evidence(root))
    drives["stuck_need"] = {
        "value": 1.0 - math.exp(-stuck / 2) if stuck else 0.0,
        "why": (f"признаков застревания: {stuck}" if stuck else "застревания не видно"),
    }

    # An event drive without results is also a treadmill: one rule for all event drives, the weight is multiplied by how often their goals succeed; no history leaves it untouched.
    for name in _EVENT_DRIVES:
        if name in drives:
            done = outcomes.get(name, [])[-LP_WINDOW:]
            factor = _learnable(done)
            if factor < 1.0:
                drives[name]["value"] *= factor
                drives[name]["why"] += f"; выходит: {sum(done)} из {len(done)}"

    # A market source exists (core/market_worker.py) but orders need the operator's yes each time, so a self-driven earning drive would contradict that.
    drives["economic_opportunity"] = {"value": 0.0,
                                      "why": "площадка подключена, но заказ — только по слову оператора; ноль по его решению"}
    return drives


def strongest(drives: dict[str, dict[str, Any]], threshold: float = 0.5) -> tuple[str, float] | None:
    """Strongest drive above the threshold, or None (nothing to wake for)."""
    name, info = max(drives.items(), key=lambda kv: kv[1]["value"])
    return (name, info["value"]) if info["value"] >= threshold else None


if __name__ == "__main__":  # pragma: no cover — manual inspection
    import sys

    ws = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
    d = compute_drives(ws)
    for name, info in sorted(d.items(), key=lambda kv: -kv[1]["value"]):
        bar = "#" * round(info["value"] * 20)
        print(f"{name:24s} {info['value']:.2f} {bar:20s} {info['why']}")
    print("strongest:", strongest(d))
