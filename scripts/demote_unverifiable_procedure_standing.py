"""One-off migration: demote active procedures whose evidence episodes are all gone.

A procedure that is active, has a non-zero success or failure count, and none of whose
source_episode_ids survive in episodic memory becomes needs_review; counts and
confidence stay untouched. Dry run by default; --apply writes after a <file>.<ts>.bak copy.

Run: python scripts/demote_unverifiable_procedure_standing.py [--apply] [--workspace DIR]
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.smart_memory import (  # noqa: E402
    EpisodicMemoryStore,
    ProceduralMemoryStore,
)


def select_and_demote(
    pstore: ProceduralMemoryStore,
    estore: EpisodicMemoryStore,
    *,
    apply: bool,
) -> dict:
    """Select and, with apply, demote unverifiable procedures; same report shape either way."""
    alive_ids = {ep.id for ep in estore.load()}

    def _unverifiable(proc) -> bool:
        return bool(
            proc.status == "active"
            and (proc.success_count or proc.failure_count)
            and proc.source_episode_ids
            and not any(ref in alive_ids for ref in proc.source_episode_ids)
        )

    if not apply:
        procedures = pstore.load()
        return {
            "scanned": len(procedures),
            "demoted": [p.id for p in procedures if _unverifiable(p)],
            "applied": False,
        }

    # Select and write under one store lock: a list read earlier would lose credit added since.
    seen: dict = {"scanned": 0, "demoted": []}

    def _demote(procedures: list) -> list:
        seen["scanned"] = len(procedures)
        out = []
        for proc in procedures:
            if _unverifiable(proc):
                seen["demoted"].append(proc.id)
                proc = replace(
                    proc, status="needs_review",
                    updated_at=datetime.now(timezone.utc).isoformat(),
                )
            out.append(proc)
        return out

    pstore.update_procedures(_demote)
    return {
        "scanned": seen["scanned"],
        "demoted": seen["demoted"],
        "applied": bool(seen["demoted"]),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workspace", default=str(ROOT))
    args = parser.parse_args()

    ws = Path(args.workspace)
    ppath = ws / "data" / "procedural_memory.jsonl"
    pstore = ProceduralMemoryStore(ppath)
    estore = EpisodicMemoryStore(ws / "data" / "episodic_memory.jsonl")

    if args.apply and ppath.is_file():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(ppath, ppath.with_name(f"{ppath.name}.{ts}.bak"))

    report = select_and_demote(pstore, estore, apply=args.apply)
    mode = "APPLIED" if report["applied"] else "DRY-RUN"
    print(f"[{mode}] scanned={report['scanned']} demoted={len(report['demoted'])}")
    for pid in report["demoted"]:
        print(f"  needs_review: {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
