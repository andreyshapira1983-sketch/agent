"""One-time migration: existing gate-wait rows stop being lessons.

WHO NEEDS THIS. The 2026-08-22 writer fix (`core/self_build_memory.py`) stops
tagging pre-flight gate refusals as `lesson` — but it is forward-only, and the
registry's own rule (MIR-115 addendum) says a repair in this family must state
which EXISTING rows it changes and how they can leave the store. This script is
that statement, executable: the live store held 64 wait-rows, all
`lesson`-protected (unevictable), all `usage_eligible=True` via the tag bypass,
43 of them byte-identical. Without this pass they would sit in the protected
set forever, exactly as MIR-058's evidence rotted while its gates were
forward-only.

WHAT IT DOES, per matching row (question is a self-build kind AND a gate-wait
status tag is present):

* removes the `lesson` tag — the row becomes ordinary: evictable by the FIFO,
  visible to `select_duplicate_episodes`;
* re-decides `usage_eligible` through the CURRENT admission rule
  (`admit_for_storage`), the same backfill principle as
  `scripts/completion_backfill.py`: replay the writer's rule, never invent a
  verdict by hand;
* then collapses byte-identical duplicates among the demoted rows via the
  existing `select_duplicate_episodes` (keeps the newest of each group) — the
  in-house mechanism MIR-090 built, which could never reach these rows while
  they were protected.

Everything else is untouched: genuine lessons keep their tag, and rows are
never deleted by this script except as exact duplicates of a surviving row.

SAFETY. Dry-run by default; `--apply` writes. A timestamped backup of the
store is written before any change. Run:

    python scripts/demote_gate_wait_lessons.py            # report only
    python scripts/demote_gate_wait_lessons.py --apply    # backup, then write
"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from core.episodic_hygiene import select_duplicate_episodes  # noqa: E402
from core.self_build_memory import _GATE_WAIT_STATUSES  # noqa: E402
from core.smart_memory import (  # noqa: E402
    EpisodeRecord,
    EpisodicMemoryStore,
    admit_for_storage,
)

_SELF_BUILD_QUESTIONS = frozenset({
    "self-build-produce", "self-apply-run", "self-task-produce", "self-task-build",
})


def _is_gate_wait_lesson(ep: EpisodeRecord) -> bool:
    tags = set(ep.tags or ())
    return (
        ep.question in _SELF_BUILD_QUESTIONS
        and "lesson" in tags
        and bool(tags & _GATE_WAIT_STATUSES)
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true",
                        help="write changes (default: report only)")
    parser.add_argument("--workspace", type=Path, default=REPO)
    args = parser.parse_args(argv)

    path = args.workspace / "data" / "episodic_memory.jsonl"
    store = EpisodicMemoryStore(path=path)
    episodes = store.load()

    matched = [e for e in episodes if _is_gate_wait_lesson(e)]
    print("Gate-wait lesson demotion (dry-run)" if not args.apply
          else "Gate-wait lesson demotion (APPLY)")
    print(f"  store: {path}")
    print(f"  episodes: {len(episodes)}   matched gate-wait lessons: {len(matched)}")
    eligible_before = sum(1 for e in matched if e.usage_eligible is True)
    print(f"  of matched, usage_eligible=True before: {eligible_before}")

    from dataclasses import replace
    demoted: dict[str, EpisodeRecord] = {}
    for ep in matched:
        stripped = replace(ep, tags=tuple(t for t in ep.tags if t != "lesson"),
                           usage_eligible=None)
        demoted[ep.id] = admit_for_storage(stripped)

    still_eligible = sum(1 for e in demoted.values() if e.usage_eligible is True)
    print(f"  after re-deciding under the current rule, eligible: {still_eligible}")

    new_rows = [demoted.get(e.id, e) for e in episodes]
    victims = set(select_duplicate_episodes(new_rows))
    dup_among_demoted = [i for i in victims if i in demoted]
    print(f"  byte-identical duplicates now collapsible: {len(dup_among_demoted)}"
          f" (of {len(victims)} store-wide)")
    survivors = [e for e in new_rows if e.id not in set(dup_among_demoted)]
    print(f"  store after: {len(survivors)} rows"
          f" ({len(episodes) - len(survivors)} duplicate rows removed)")

    if not args.apply:
        print("\n  dry-run: nothing written. Re-run with --apply.")
        return 0

    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    backup = path.with_suffix(f".jsonl.pre-gate-wait-demotion-{stamp}.bak")
    shutil.copy2(path, backup)
    print(f"\n  backup: {backup}")

    from core.state_integrity import rewrite_state_jsonl_unlocked, state_file_lock
    with state_file_lock(path):
        rewrite_state_jsonl_unlocked(path, [e.to_dict() for e in survivors])
    print(f"  written: {len(survivors)} rows")

    check = EpisodicMemoryStore(path=path).load()
    left = [e for e in check if _is_gate_wait_lesson(e)]
    print(f"  verify: gate-wait lessons remaining: {len(left)}")
    return 0 if not left else 1


if __name__ == "__main__":
    raise SystemExit(main())
