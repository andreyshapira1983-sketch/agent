"""CEO tier-0 file line counts vs soft ceilings.

See docs/CENTRAL_AGENT_GOVERNANCE.md. Read-only; does not modify the repo.

Usage:
    python scripts/check_ceo_file_baseline.py
"""
from __future__ import annotations

from pathlib import Path

WATCH: dict[str, int] = {
    "core/loop.py": 4500,
    "main.py": 2000,
    "core/planner.py": 1800,
    "agent_tick.py": 1300,
    "core/autonomous_runtime.py": 1150,
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
