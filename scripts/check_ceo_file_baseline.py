"""Check line counts of the largest (CEO tier-0) files against their ratchet ceilings."""
from __future__ import annotations

from pathlib import Path

# Each ceiling is the measured size plus small slack: a watched file may not grow back.
# Lower a ceiling when a file shrinks; raise one only with a one-line reason beside it.
# Aspirational sizes go in the comment; reaching them is task work, not this guard's job.
WATCH: dict[str, int] = {
    # Split into core/loop_* modules (operator cap 2000); later growth: the run-identity edge.
    "core/loop.py": 761,
    "main.py": 40,  # launcher only; the CLI lives in cli/app.py
    # Growth: tool argument contracts, scoped lesson door, step rationale, hidden tool blocks.
    "core/planner.py": 597,
    # Growth: charter/drive goal selection, budget-config and status lines, daily snapshot,
    # queue repair. Aspiration 1300: the goal resolver lives in core/charter_goal.py.
    "agent_tick.py": 2238,
    # Growth: unattended-path permissions with recorded reasons, standing grants, outcome
    # accounting, goal verdicts, incident updates. Aspiration 1150.
    "core/autonomous_runtime.py": 1387,
    # Growth: episode-admission gates, procedure locking and repair, replay input digests.
    "core/smart_memory.py": 2017,
    # Growth: 22 command-parsing holes closed (search, git, PATH, links, key files).
    "tools/shell_exec.py": 1390,
    # Growth: per-file approval gate, Python body vetoes, no split without proof.
    "core/self_build_producer.py": 1901,
    # Growth: billed streaming, declared-model resolution, failover reasons in route_reason.
    "core/model_router.py": 1880,
    # Growth: web_fetch URL rechecked after link substitution (_url_refusal); _subagent_reason.
    "core/step_sanitizer.py": 1352,
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
