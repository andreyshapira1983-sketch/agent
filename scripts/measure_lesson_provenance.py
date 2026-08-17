"""CLI for the causal-provenance meter: render every claim's receipt chain.

Usage: python scripts/measure_lesson_provenance.py [lesson_key ...]
With no keys, walks every claim in the store. Read-only.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.causal_claim_store import load_claims
from core.lesson_provenance import trace_lesson_provenance


def main(argv: list[str]) -> int:
    workspace = Path(__file__).resolve().parents[1]
    keys = argv or [extra["key"] for _c, extra in load_claims(workspace)]
    for key in keys:
        print(trace_lesson_provenance(workspace, key).render())
        print()
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
