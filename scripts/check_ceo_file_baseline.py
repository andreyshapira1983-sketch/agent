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
    "core/loop.py": 740,                  # разбор на модули (правило оператора: потолок 2000): −828 строк ушло в core/loop_step_execution, −234 в core/loop_response_deciders, −423 в core/loop_synthesis, −106 в core/loop_evidence_chain, −123 в core/loop_verification, −122 в core/loop_observe, −165 в core/loop_run_tail, −57 и −63 в loop_evidence_chain/loop_context, −375 в core/loop_attempt, −341 в core/loop_verify_replan, −254 в core/loop_init, −84 в core/loop_synthesis, −158 мелких методов, −139 ворот в core/loop_gates, −97 пролога и обязательств; оркестратор
    "main.py": 2000,                       # 47 today; the old extraction's win
    "core/planner.py": 560,                # measured 516 after piece 5 (host-tools context out)
    "agent_tick.py": 1500,                 # measured 1458; aspiration 1300
    "core/autonomous_runtime.py": 1400,    # measured 1364; aspiration 1150
    "core/smart_memory.py": 1900,          # measured 1861 after the causal-credit split + outcome extraction
    "core/self_build_producer.py": 1841,   # 1860 → 1841: reply diagnosis moved out to core/builder_reply_diagnosis.py (MIR-084)
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
