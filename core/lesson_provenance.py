"""Causal-provenance meter for lessons (read-only, no delivery organ here).

A lesson's use is a chain of receipts: derived_from -> injected -> acted ->
measured. PROVEN needs an independent machine receipt (episodic store record,
injection-journal row); the claim's own prose reaches at most SELF_DECLARED;
nothing behind a link is ABSENT. Design prose: docs/CODE_NOTES.md
(«The provenance meter reads receipts, not prose»).
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from core.causal_claim_store import load_claims

#: The delivery receipt the meter reads. Nobody writes it yet — measuring
#: that absence is the meter's first job; the future delivery code must
#: append one row per lesson actually placed into a prompt.
INJECTION_JOURNAL = Path("data") / "lesson_injections.jsonl"

_EPISODIC = Path("data") / "episodic_memory.jsonl"

_CHAIN = ("derived_from", "injected", "acted", "measured")


@dataclass(frozen=True)
class ProvenanceLink:
    name: str
    status: str  # PROVEN | SELF_DECLARED | ABSENT
    detail: str
    refs: tuple[str, ...] = ()


@dataclass(frozen=True)
class ProvenanceReport:
    lesson_key: str
    state: str
    links: tuple[ProvenanceLink, ...]
    verdict: str
    missing: tuple[str, ...]

    def render(self) -> str:
        lines = [f"lesson {self.lesson_key} [{self.state}]"]
        for link in self.links:
            refs = f"  refs: {', '.join(link.refs)}" if link.refs else ""
            lines.append(f"  {link.name:12s} {link.status:13s} {link.detail}{refs}")
        lines.append(f"  verdict: {self.verdict}"
                     + (f" (missing: {', '.join(self.missing)})" if self.missing else ""))
        return "\n".join(lines)


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.exists():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rec = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(rec, dict):
            payload = rec.get("payload")
            rows.append(payload if isinstance(payload, dict) else rec)
    return rows


def _episode_exists(workspace: Path, episode_id: str) -> bool:
    return any(
        str(row.get("id") or "") == episode_id
        for row in _read_jsonl(workspace / _EPISODIC)
    )


def _resolve_measurement_ref(workspace: Path, ref: str) -> bool:
    kind, _, ident = ref.partition(":")
    if kind == "episode" and ident:
        return _episode_exists(workspace, ident)
    return False


def trace_lesson_provenance(workspace: str | Path, lesson_key: str) -> ProvenanceReport:
    """Assemble the receipt chain for one lesson. Reads only; invents nothing."""
    ws = Path(workspace)
    found = next(
        ((claim, extra) for claim, extra in load_claims(ws)
         if extra.get("key") == lesson_key),
        None,
    )
    if found is None:
        return ProvenanceReport(
            lesson_key=lesson_key, state="not_found", links=(),
            verdict="CAUSAL USE NOT PROVEN", missing=_CHAIN,
        )
    claim, _extra = found
    from core.causal_lesson import state_of

    links: list[ProvenanceLink] = []

    # 1. derived_from — the origin episode must be a store record, not a name.
    episode_id = claim.observation.episode_id
    if not episode_id:
        links.append(ProvenanceLink(
            "derived_from", "ABSENT", "the claim names no origin episode"))
    elif _episode_exists(ws, episode_id):
        links.append(ProvenanceLink(
            "derived_from", "PROVEN",
            "origin episode is a record in the episodic store",
            (episode_id,)))
    else:
        links.append(ProvenanceLink(
            "derived_from", "SELF_DECLARED",
            "the claim names an episode the episodic store does not hold",
            (episode_id,)))

    # 2. injected — only the delivery journal can prove a prompt carried it.
    journal_path = ws / INJECTION_JOURNAL
    receipts = [
        row for row in _read_jsonl(journal_path)
        if str(row.get("lesson_key") or "") == lesson_key
    ]
    if not journal_path.exists():
        links.append(ProvenanceLink(
            "injected", "ABSENT",
            f"no injection journal exists ({INJECTION_JOURNAL.as_posix()}) — "
            "delivery is structurally unobservable today"))
    elif not receipts:
        links.append(ProvenanceLink(
            "injected", "ABSENT",
            "the injection journal holds no receipt for this lesson"))
    else:
        links.append(ProvenanceLink(
            "injected", "PROVEN",
            f"{len(receipts)} delivery receipt(s) in the injection journal",
            tuple(str(r.get("run_id") or "?") for r in receipts)))

    # 3-4. acted / measured — receipts first; the claim's own intervention
    # prose is the claim testifying about itself, never above SELF_DECLARED.
    action_refs = tuple(
        str(r.get("action_ref")) for r in receipts if r.get("action_ref"))
    measurement_refs = tuple(
        str(r.get("measurement_ref")) for r in receipts if r.get("measurement_ref"))
    has_prose = claim.intervention is not None

    if action_refs:
        links.append(ProvenanceLink(
            "acted", "PROVEN",
            "a delivery receipt names the action taken", action_refs))
    elif has_prose:
        links.append(ProvenanceLink(
            "acted", "SELF_DECLARED",
            "only the claim's own intervention prose names an action"))
    else:
        links.append(ProvenanceLink(
            "acted", "ABSENT", "no receipt and no prose name any action"))

    resolved = tuple(
        ref for ref in measurement_refs if _resolve_measurement_ref(ws, ref))
    if resolved:
        links.append(ProvenanceLink(
            "measured", "PROVEN",
            "the measurement ref resolves to a store record", resolved))
    elif measurement_refs:
        links.append(ProvenanceLink(
            "measured", "ABSENT",
            "a receipt names a measurement the stores do not hold",
            measurement_refs))
    elif has_prose:
        links.append(ProvenanceLink(
            "measured", "SELF_DECLARED",
            "only the claim's own intervention prose reports an outcome"))
    else:
        links.append(ProvenanceLink(
            "measured", "ABSENT", "no receipt and no prose report any outcome"))

    missing = tuple(link.name for link in links if link.status != "PROVEN")
    return ProvenanceReport(
        lesson_key=lesson_key,
        state=state_of(claim),
        links=tuple(links),
        verdict="CAUSAL USE PROVEN" if not missing else "CAUSAL USE NOT PROVEN",
        missing=missing,
    )
