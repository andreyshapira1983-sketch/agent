"""Records the agent's self-stop decision as a single journal entry so the next run
can reconstruct why and how the agent stopped.

Contract: one journal per stop; fields come from the runtime/verifier, never
invented by the model; without a write source there is no record.
"""
import hashlib
from pathlib import Path
from typing import Any

from core.causal_lesson import observation_from_self_stop
from core.causal_store import CausalObservationStore
from core.state_integrity import append_state_jsonl_unlocked, state_file_lock

ALLOWED_PATH = "data/self_stops.jsonl"
ALLOWED_KINDS = {"goal_selection_failure", "budget_stop", "plan_parse_failed", "approval_denied", "other"}


def _normalize(value):
    return " ".join(str(value).strip().split())


def _wall_signature(kind, reason, source, verdict_ref=None):
    parts = [_normalize(kind), _normalize(reason), _normalize(source)]
    if verdict_ref is not None:
        parts.append(_normalize(verdict_ref))
    return hashlib.sha256("|".join(parts).encode("utf-8")).hexdigest()


def reason_kind(text: str) -> str:
    """Return a stable canonical category for a pick.reason string.

    Categories are machine words, not phrases. They depend only on the
    kind of reason, never on numbers, timestamps, or foreign text.
    """
    lowered = text.lower()

    if "repeats a recent campaign goal" in lowered:
        return "goal_repeat"
    if "outside" in lowered and ".." in text:
        return "goal_length"
    if "widen the agent's own authority" in lowered:
        return "authority_widen"

    return "other"


def record_self_stop(kind, source, reason, episode_id=None, run_id=None, ts=None, verdict_ref=None, outcome=None) -> dict:
    if not source or not _normalize(source):
        return {"recorded": False, "path": ALLOWED_PATH, "signature": "", "error": "source is required"}
    if kind not in ALLOWED_KINDS:
        return {"recorded": False, "path": ALLOWED_PATH, "signature": "", "error": f"kind must be one of {sorted(ALLOWED_KINDS)}"}
    signature = _wall_signature(kind, reason, source, verdict_ref)
    record = {
        "episode_id": episode_id,
        "run_id": run_id,
        "kind": kind,
        "ts": ts,
        "source": source,
        "verdict_ref": verdict_ref,
        "reason": reason,
        "outcome": outcome,
        "signature": signature,
    }
    with state_file_lock(ALLOWED_PATH):
        append_state_jsonl_unlocked(ALLOWED_PATH, [record])
    return {"recorded": True, "path": ALLOWED_PATH, "signature": signature}


def record_stop_observation(
    workspace: Any, *, kind: str, reason: str, signature: str, source: str,
    run_id: str = "", trace_id: str = "", episode_id: str = "",
) -> str:
    """Положить остановку в поток наблюдений — повод для лестницы объяснений.

    Возвращает отпечаток записанного наблюдения или "" — когда повода нет
    (пустые поля) или запись не удалась. Провал ЗДЕСЬ не имеет права ломать
    основной путь: остановка уже случилась, и потерянный повод к расследованию
    хуже, чем упавший прогон, только для нас — а упавший прогон хуже для всего.
    """
    try:
        observation = observation_from_self_stop(
            kind=kind, reason=reason, signature=signature, source=source,
            run_id=run_id, trace_id=trace_id, episode_id=episode_id,
        )
        if observation is None:
            return ""
        store = CausalObservationStore(
            Path(workspace) / "data" / "causal_observations.jsonl"
        )
        return store.record(observation).fingerprint
    except (OSError, ValueError):
        return ""
