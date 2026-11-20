"""A repeated own failure becomes a registry defect via `upsert_failure`, not just an observation.

    A single observation is not registered: once is a case, twice is a class."""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

MIN_OCCURRENCES = 2
MAX_PER_SWEEP = 5

#: Path in the observation text: trace, journal or module address.
_PATH_RE = re.compile(r"[\w./-]+\.(?:jsonl|json|py|md|txt|log)\b")


def _keep_paths(text: str, limit: int) -> str:
    """Truncate the description without losing the paths used to verify it.

    The trace path sits at the END of the observation, so plain length truncation
    cut it mid-name."""
    text = str(text or "")
    if len(text) <= limit:
        return text
    head = text[:limit].rsplit(" ", 1)[0]
    lost = [p for p in _PATH_RE.findall(text) if p not in head]
    return head + (" (адреса: " + ", ".join(dict.fromkeys(lost)) + ")" if lost else "")


def intake_observations(workspace: Path | str, registry: Any = None) -> list[str]:
    """Register repeated observations as defects; return their fingerprints."""
    from core.causal_store import CausalObservationStore
    from core.self_improvement_issues import DEFAULT_ISSUE_PATH, SelfImprovementIssueRegistry

    root = Path(workspace)
    registry = registry or SelfImprovementIssueRegistry(root / DEFAULT_ISSUE_PATH)
    # An accepted observation is recognised by its own evidence (cobs_...), not by `related_error_text`, whose text never equals the fingerprint.
    known = {str(ref) for issue in registry.list() for ref in (issue.evidence or ())}
    filed: list[str] = []
    records = CausalObservationStore(root / "data" / "causal_observations.jsonl").load()
    for record in sorted(records, key=lambda r: (-r.occurrences, r.last_seen))[:40]:
        if record.occurrences < MIN_OCCURRENCES or record.fingerprint in known:
            continue
        # One defect per signal, not per combination: the registry derives the class from the signals string, so a combination would create its own class.
        body = _keep_paths(record.observed_mismatch, 300)
        for signal in (record.defect_signals or ("detector",)):
            # The text starts with "detectors": the registry derives class fingerprint and title from it (core/self_improvement_issues.py).
            text = (f"detectors {signal}: {body} "
                    f"(повторений: {record.occurrences}; улики: "
                    f"{', '.join(record.evidence_refs[:3])})")
            issue = registry.upsert_failure(text, record.last_seen)
            registry.transition(status=issue.status, observed_at=record.last_seen,
                                fingerprint=issue.fingerprint, evidence=record.fingerprint)
        filed.append(record.fingerprint)
        if len(filed) >= MAX_PER_SWEEP:
            break
    return filed
