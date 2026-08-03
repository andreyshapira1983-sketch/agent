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
    "core/loop.py": 4165,                  # was 4500; decomposition banked 4047; +MIR-069 (verification_explained), +MIR-073 (budget disagreement), +MIR-075 (ask-back on unsupported self-analysis) — all reviewed wirings, ~30-45 lines each
    "main.py": 2000,                       # 47 today; the old extraction's win
    "core/planner.py": 560,                # measured 516 after piece 5 (host-tools context out)
    "agent_tick.py": 1500,                 # measured 1458; aspiration 1300
    "core/autonomous_runtime.py": 1400,    # measured 1364; aspiration 1150
    "core/smart_memory.py": 1900,          # measured 1861 after the causal-credit split + outcome extraction
    "core/self_build_producer.py": 1860,   # measured 1813 after MIR-071 raw-retention (reviewed growth: preservation helper + honest critic wording)
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
