"""CEO tier-0 file line counts vs soft ceilings.

See docs/CENTRAL_AGENT_GOVERNANCE.md. Read-only; does not modify the repo.

Usage:
    python scripts/check_ceo_file_baseline.py
"""
from __future__ import annotations

from pathlib import Path

# Ceilings are a RATCHET, not an aspiration: each is the measured size at the
# last review plus small slack, so the guard's one job is "this file may not
# grow back". When a decomposition lands (loop.py: 4733 -> 4047 via #217-#224),
# LOWER the ceiling to bank the win. Aspirational targets live in the comment
# column; reaching one is task-list work (planner.py is task #5), not this
# guard's business.
#
# Found orphaned by the 2026-08 audit: this script was wired into nothing, so
# three files sat over their ceilings with the guard reporting it to nobody.
# It now runs inside the test suite (tests/test_file_size_ratchet.py).
WATCH: dict[str, int] = {
    "core/loop.py": 4100,                  # was 4500; decomposition banked at 4047
    "main.py": 2000,                       # 47 today; the old extraction's win
    "core/planner.py": 1300,               # measured 1257 after piece 2 (doc routing out); aspiration met
    "agent_tick.py": 1500,                 # measured 1458; aspiration 1300
    "core/autonomous_runtime.py": 1400,    # measured 1364; aspiration 1150
    "core/smart_memory.py": 1800,          # measured 1741 — new watch, top-3 giant
    "core/self_build_producer.py": 1800,   # measured 1755 — new watch
    "core/model_router.py": 1800,          # measured 1719 — new watch
}


def main() -> int:
    root = Path(__file__).resolve().parent.parent
    exit_code = 0
    for rel, ceiling in WATCH.items():
        path = root / rel
        n = len(path.read_text(encoding="utf-8").splitlines())
        if n > ceiling:
            flag = "REVIEW"
            exit_code = 1
        else:
            flag = "ok"
        print(f"{flag:6s}  {n:5d} / {ceiling}  {rel}")
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
