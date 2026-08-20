"""Is the production subject still the frozen snapshot S?"""
from __future__ import annotations

import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

#: The production runtime. Anything the agent executes to serve a request.
SUBJECT_PATHS = ("core", "cli", "app", "api", "tools", "agent_tick.py", "main.py")

SNAPSHOT = "f88997395abd7ade9f5a7bbb7e7e57db7a2cd1ee"

INTACT, TRACKED_DRIFT, UNTRACKED_ADDITION, BOTH, UNREADABLE = 0, 1, 2, 3, 4


def check(snapshot: str = SNAPSHOT, root: Path = ROOT) -> tuple[int, list[str]]:
    """Return (verdict code, human-readable evidence lines)."""
    queries = {
        "tracked": ["git", "diff", "--name-only", snapshot, "--", *SUBJECT_PATHS],
        "untracked": ["git", "ls-files", "--others", "--exclude-standard", "--",
                      *SUBJECT_PATHS],
    }
    seen: dict[str, list[str]] = {}
    for label, argv in queries.items():
        # `check=False` on purpose: a non-zero git exit is DATA here — it means
        # the snapshot is unreadable, which is a verdict, not a crash.
        proc = subprocess.run(  # nosec B603 B607
            argv, cwd=root, capture_output=True, text=True, check=False,
            encoding="utf-8", errors="replace", timeout=120)
        if proc.returncode != 0:
            return UNREADABLE, [f"{label}: git exited {proc.returncode}",
                                proc.stderr.strip()]
        seen[label] = [line for line in proc.stdout.splitlines() if line.strip()]

    code = INTACT
    if seen["tracked"]:
        code |= TRACKED_DRIFT
    if seen["untracked"]:
        code |= UNTRACKED_ADDITION
    evidence = [f"tracked drift from {snapshot[:7]}: {path}"
                for path in seen["tracked"]]
    evidence += [f"untracked addition: {path}" for path in seen["untracked"]]
    return code, evidence or [f"subject matches {snapshot[:7]}"]


_NAMES = {INTACT: "INTACT", TRACKED_DRIFT: "TRACKED_DRIFT",
          UNTRACKED_ADDITION: "UNTRACKED_ADDITION", BOTH: "BOTH",
          UNREADABLE: "UNREADABLE"}

if __name__ == "__main__":
    _code, _evidence = check()
    for _line in _evidence:
        print(_line)
    print(f"QM-SUBJECT: {_NAMES.get(_code, 'UNKNOWN')} exit={_code}")
    raise SystemExit(_code)
