"""What the completion axis says about memory written before it existed.

Read-only. This script diagnoses, it does not repair: it never writes to a
store, never migrates an episode, and never resets a counter. Run it to find
out what the MIR-057 gates would do with the records already on disk.

Two questions it exists to answer.

**Is there anything to migrate?** An episode banked before the axis carries no
`completion_state`, and the cycle's assembly table cannot reconstruct one: of the
facts it reads, only `replan_exhausted` is stored, while `aborted_reason` and the
synthesizer's declaration were never persisted. So for cycle-banked records the
count is zero, and that number belongs in a report rather than in an assumption.
Two *writers*, however, settle the axis from evidence they do persist, and rows
they banked before that fix are recoverable. `scripts/completion_backfill.py` owns
that decision; this report calls it rather than restating it, so the diagnostic
and the migration can never disagree about which rows qualify.

**What did the old rule already buy?** The gates stop new credit, they do not
unwind old credit. A procedure whose counters were earned under the previous
rule keeps them, stays `active`, and keeps being injected into planning. This
report quantifies that, carefully: `source_episode_ids` proves a procedure was
UPDATED BY an episode, not that a particular increment came from it. There is
no per-increment ledger, so the honest phrasing is "procedures with non-zero
counters associated with legacy episodes", and the limits of what can be
attributed are printed alongside the numbers.

    python scripts/completion_legacy_report.py [--workspace PATH]
"""
from __future__ import annotations

import argparse
import hashlib
import sys
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from core.loop import AgentLoop
from core.smart_memory import (
    EpisodeRecord,
    EpisodicMemoryStore,
    ProceduralMemoryStore,
    ProcedureRecord,
    effective_completion,
    is_usage_eligible,
    procedure_credit_allowed,
    procedure_debit_allowed,
)
from scripts.completion_backfill import writer_backfill_verdict

# Similarity is the question-shaped half of the fast-path gate and says
# nothing about the episode. Held at the maximum so the report measures only
# what the RECORD would allow.
_MAX_SIMILARITY = 1.0


def _is_lesson(episode: EpisodeRecord) -> bool:
    return "lesson" in episode.tags


def _passes_positive_reuse(episode: EpisodeRecord) -> bool:
    """The retrieval filter's ordinary arm — a lesson does not come this way."""
    if _is_lesson(episode):
        return False
    if episode.outcome != "success":
        return False
    if effective_completion(episode) != "achieved":
        return False
    return is_usage_eligible(episode)


def _passes_lesson_context(episode: EpisodeRecord) -> bool:
    """Admission through the curated-lesson arm, which ignores completion.

    Surfacing a past failure as a warning is the whole purpose of the tag, so
    this is a deliberate exception and is counted on its own line, never as an
    anomaly.

    Reads `is_usage_eligible` — the STORED bit — because that is what
    retrieval reads. `decide_usage_eligibility` is the banking-time policy and
    answers a different question: for a lesson it returns True whatever the
    stored bit says. Using it here reported 108 legacy lessons as admitted
    when the live agent admits none of them, and a diagnostic that overstates
    what memory is reachable is worse than no diagnostic.
    """
    return _is_lesson(episode) and is_usage_eligible(episode)


def _passes_fast_path(episode: EpisodeRecord) -> bool:
    return AgentLoop._fast_path_allows_replay(episode, _MAX_SIMILARITY)


def _backfill_candidate(episode: EpisodeRecord) -> bool:
    """Would the migration settle this record's verdict?

    Delegates to `scripts/completion_backfill.py`, the single decision point, so a
    row this report calls a candidate is exactly a row the migration would
    write, and nothing else. Restating the rule here is how a diagnostic starts
    lying about a migration it no longer matches.

    `replan_exhausted` alone does not qualify: the cycle's assembly table also
    needs `aborted_reason` and a declaration, neither of which was persisted, so
    no migration implements that path.
    """
    return writer_backfill_verdict(episode.to_dict()) is not None


def _distribution(values) -> dict:
    return dict(sorted(Counter(values).items(), key=lambda kv: (-kv[1], str(kv[0]))))


def _hash(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path.exists() else "<absent>"


def build_report(episodes: list[EpisodeRecord], procedures: list[ProcedureRecord]) -> dict:
    """Pure: the same inputs always produce the same report."""
    live_ids = {e.id for e in episodes}
    legacy_ids = {e.id for e in episodes if e.completion_state is None}

    gates = {
        "lesson context eligibility": [e for e in episodes if _passes_lesson_context(e)],
        "positive episodic reuse": [e for e in episodes if _passes_positive_reuse(e)],
        "episodic fast-path": [e for e in episodes if _passes_fast_path(e)],
        "procedural credit": [e for e in episodes if procedure_credit_allowed(e)],
        "procedural debit": [e for e in episodes if procedure_debit_allowed(e)],
    }

    # An anomaly is an UNCLASSIFIED record reaching a gate that is supposed to
    # require `achieved` or a demonstrated failure. The lesson arm is not one:
    # it is the agreed exception and is reported separately above.
    anomalies = {
        name: [e.id for e in eps if effective_completion(e) == "unknown"]
        for name, eps in gates.items()
        if name != "lesson context eligibility"
    }

    touched = []
    for proc in procedures:
        refs = list(proc.source_episode_ids)
        legacy_refs = [r for r in refs if r in legacy_ids]
        if not legacy_refs:
            continue
        if (proc.success_count + proc.failure_count) == 0:
            continue
        touched.append({
            "id": proc.id,
            "workflow_key": proc.workflow_key,
            "success": proc.success_count,
            "failure": proc.failure_count,
            "confidence": proc.confidence,
            "status": proc.status,
            "refs_total": len(refs),
            "refs_legacy": len(legacy_refs),
            "refs_missing": len([r for r in refs if r not in live_ids]),
        })

    return {
        "episodes": len(episodes),
        "procedures": len(procedures),
        "raw_completion_state": _distribution(
            ("<missing>" if e.completion_state is None else e.completion_state)
            for e in episodes
        ),
        "effective_completion": _distribution(effective_completion(e) for e in episodes),
        "declared_completion": _distribution(
            ("<none>" if e.declared_completion is None else e.declared_completion)
            for e in episodes
        ),
        # Reported because on a legacy store this — not completion — is what
        # actually withholds everything: retrieval admits only an explicit
        # True, and a row written before the field carries no bit at all.
        "usage_eligible": _distribution(
            ("<missing>" if e.usage_eligible is None else str(e.usage_eligible))
            for e in episodes
        ),
        "gates": {name: len(eps) for name, eps in gates.items()},
        "anomalies": {k: v for k, v in anomalies.items() if v},
        "backfill_candidates": [e.id for e in episodes if _backfill_candidate(e)],
        "procedures_with_legacy_refs": touched,
        "confidence_distribution": _distribution(p["confidence"] for p in touched),
        "status_distribution": _distribution(p["status"] for p in touched),
    }


def render(report: dict) -> str:
    out: list[str] = []
    add = out.append
    add("COMPLETION AXIS — LEGACY REPORT (read-only, MIR-057)")
    add(f"  episodes: {report['episodes']}   procedures: {report['procedures']}")

    add("\nRAW completion_state as stored ('<missing>' = the key is absent)")
    for key, count in report["raw_completion_state"].items():
        add(f"    {key:<22} {count:>5}")
    add("EFFECTIVE completion (the single accessor every reader uses)")
    for key, count in report["effective_completion"].items():
        add(f"    {key:<22} {count:>5}")
    add("DECLARED completion as stored (auditable history, read by no gate)")
    for key, count in report["declared_completion"].items():
        add(f"    {key:<22} {count:>5}")
    add("STORED eligibility bit — what retrieval reads; only an explicit True admits")
    for key, count in report["usage_eligible"].items():
        add(f"    {key:<22} {count:>5}")

    add("\nGATES — what each would admit today")
    for name, count in report["gates"].items():
        note = ""
        if name == "lesson context eligibility":
            note = "  (expected exception: a lesson ignores completion by design)"
        add(f"    {name:<28} {count:>5}{note}")

    add("\nMIGRATION INPUT")
    add(f"    safe episode backfill candidates: {len(report['backfill_candidates'])}")
    add("    Counted by `scripts/completion_backfill.py`, the same decision the\n"
        "    migration makes: a record qualifies only when a writer signature is\n"
        "    proved first and that writer's own durable evidence is present.\n"
        "    Cycle-banked records stay unclassifiable rather than merely\n"
        "    unclassified — `aborted_reason` was never persisted and a missing\n"
        "    declaration cannot be recovered.")

    touched = report["procedures_with_legacy_refs"]
    add("\nLEGACY-UNVERIFIABLE PROCEDURAL CREDIT")
    add(f"    procedures with non-zero counters and legacy source references: "
        f"{len(touched)} of {report['procedures']}")
    if touched:
        add(f"    aggregate success={sum(p['success'] for p in touched)} "
            f"failure={sum(p['failure'] for p in touched)}")
        add("    legacy-source coverage per procedure "
            "(legacy refs / total refs / refs no longer in store):")
        for p in sorted(touched, key=lambda p: -p["refs_legacy"])[:12]:
            add(f"      {p['workflow_key'][:44]:<46} "
                f"{p['refs_legacy']:>3}/{p['refs_total']:<3} "
                f"missing={p['refs_missing']:<3} "
                f"s={p['success']} f={p['failure']} conf={p['confidence']} {p['status']}")
        add(f"    confidence distribution: {report['confidence_distribution']}")
        add(f"    status distribution:     {report['status_distribution']}")

    add("\nWHAT CANNOT BE ATTRIBUTED")
    add("    `source_episode_ids` is an applied-feedback journal: it proves a")
    add("    procedure was UPDATED BY an episode, not that a given increment came")
    add("    from it. Counters are aggregates and no per-increment ledger exists,")
    add("    so individual increments are NOT attributable to individual episodes.")
    add("    References to episodes already evicted by the store's FIFO window")
    add("    cannot be examined at all — those are counted as `missing` above.")

    add("\nANOMALIES (an unclassified record reaching a gate that requires a verdict)")
    if report["anomalies"]:
        for name, ids in report["anomalies"].items():
            add(f"    !! {name}: {len(ids)} — {ids[:5]}")
    else:
        add("    none")
    return "\n".join(out)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(ROOT))
    args = parser.parse_args(argv)

    data = Path(args.workspace) / "data"
    episodic = data / "episodic_memory.jsonl"
    procedural = data / "procedural_memory.jsonl"
    before = (_hash(episodic), _hash(procedural))

    episodes = EpisodicMemoryStore(episodic).load()
    procedures = ProceduralMemoryStore(procedural).load()
    print(render(build_report(episodes, procedures)))

    # Self-check rather than a promise: a diagnostic that quietly wrote would
    # be worse than one that never ran.
    after = (_hash(episodic), _hash(procedural))
    if before != after:
        print("\nERROR: a store changed while reporting — this script must be "
              "read-only", file=sys.stderr)
        return 1
    print("\nstores unchanged (sha256 verified before and after)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
