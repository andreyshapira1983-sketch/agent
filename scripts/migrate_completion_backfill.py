"""Settle the completion axis on episodes banked before their writer learned to.

Nine rows in the live store carry no completion verdict while
`admit_for_storage` marks them `usage_eligible=True` (MIR-042, so lessons stop
being invisible). Retrieval therefore replays them carrying no verdict at all —
the one thing `test_no_legacy_episode_in_the_live_store_is_ever_admitted`
forbids. The writers that banked them now settle the axis; this script settles it
for what they wrote earlier.

**What it will not do.** It does not classify records generally. Every decision
comes from :func:`scripts.completion_backfill.writer_backfill_verdict`, which proves
a writer signature *before* consulting the shared table — so a cycle-banked row
is left untouched even when its `outcome` looks mappable. Rows that already carry
a verdict are never re-decided (MIR-057: the axis is settled once).

**How it writes.** Rows are integrity-envelope encoded, so hand-editing the file
corrupts it. The rewrite goes through `state_file_lock` +
`rewrite_state_jsonl_unlocked`, the same path the store itself uses when pruning
(`core/smart_memory.py:505-532`), and only the `completion_state` key is added —
no `from_dict`/`to_dict` round trip that could silently normalise other fields.

Dry-run is the default. `--apply` writes, after taking a timestamped backup.

    python scripts/migrate_completion_backfill.py            # show the plan
    python scripts/migrate_completion_backfill.py --apply    # write it
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from scripts.completion_backfill import writer_backfill_verdict  # noqa: E402
from core.state_integrity import (  # noqa: E402
    backup_state_file,
    read_state_jsonl_unlocked,
    rewrite_state_jsonl_unlocked,
    state_file_lock,
)


def plan_backfill(payloads: list[dict[str, Any]]) -> list[tuple[int, str]]:
    """Pure: which rows would change, and to what. No I/O, no mutation."""
    plan: list[tuple[int, str]] = []
    for index, payload in enumerate(payloads):
        verdict = writer_backfill_verdict(payload)
        if verdict:
            plan.append((index, verdict))
    return plan


def describe(payloads: list[dict[str, Any]], plan: list[tuple[int, str]]) -> str:
    out: list[str] = []
    unset = [p for p in payloads if not p.get("completion_state")]
    out.append(f"episodes: {len(payloads)}   without a verdict: {len(unset)}")
    out.append(f"backfillable (writer signature proved): {len(plan)}")

    if plan:
        out.append("\nWOULD SETTLE")
        for index, verdict in plan:
            row = payloads[index]
            out.append(
                f"    {row.get('id', '<no id>')}  {row.get('question', '')!r}"
                f"  outcome={row.get('outcome')}  ->  {verdict}"
            )

    planned = {i for i, _ in plan}
    skipped = [
        p for i, p in enumerate(payloads)
        if i not in planned and not p.get("completion_state")
    ]
    out.append(f"\nLEFT UNCLASSIFIED (no writer signature): {len(skipped)}")
    for row in skipped:
        out.append(f"    {row.get('id', '<no id>')}  {row.get('question', '')!r}")
    return "\n".join(out)


def _backup(path: Path) -> Path:
    return backup_state_file(path)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--workspace", default=str(ROOT))
    parser.add_argument(
        "--apply",
        action="store_true",
        help="write the change (default is a dry run that touches nothing)",
    )
    args = parser.parse_args(argv)

    path = Path(args.workspace) / "data" / "episodic_memory.jsonl"
    if not path.exists():
        print(f"no episodic store at {path} — nothing to do")
        return 0

    # The lock is held across read and write: a tick banking an episode midway
    # would otherwise be dropped by the rewrite.
    with state_file_lock(path):
        payloads = read_state_jsonl_unlocked(path)
        plan = plan_backfill(payloads)
        print(describe(payloads, plan))

        if not plan:
            print("\nnothing to settle — store unchanged")
            return 0
        if not args.apply:
            print("\ndry run — store unchanged. Re-run with --apply to write.")
            return 0

        backup = _backup(path)
        for index, verdict in plan:
            payloads[index]["completion_state"] = verdict
        rewrite_state_jsonl_unlocked(path, payloads)

    print(f"\nbackup: {backup}")
    print(f"settled {len(plan)} episode(s); {len(payloads)} row(s) rewritten")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
