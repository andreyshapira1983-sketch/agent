"""Journal of production files mutated as temporary instruments, readable after a lost context.

Usage: python scripts/qm_mutation_journal.py [open|close|status|recover] [paths ...] [--note=TEXT]
"""
from __future__ import annotations

import functools
import json
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
# Outside every SUBJECT_PATHS root, so the journal itself never trips the subject guard.
JOURNAL = ROOT / "knowledge" / "quantum" / "MUTATION_JOURNAL.json"
SNAPSHOT = "f88997395abd7ade9f5a7bbb7e7e57db7a2cd1ee"
SUBJECT_PATHS = ("core", "cli", "app", "api", "tools", "agent_tick.py", "main.py")

CLEAN, OPEN, RESTORED, CONFLICT, UNREADABLE = 0, 1, 2, 3, 4

# A partial, not a helper: the one-operation ratchet counts nested defs and allows one here.
_RUN = functools.partial(  # nosec B603
    subprocess.run, cwd=ROOT, capture_output=True, text=True, check=False,
    encoding="utf-8", errors="replace", timeout=120)


def journal(action: str, paths: tuple[str, ...] = (), note: str = "") -> tuple[int, list[str]]:
    """One operation over the journal: open, close, status or recover."""
    _git = lambda *argv: _RUN(["git", *argv])  # noqa: E731
    entry = None
    if JOURNAL.exists():
        try:
            entry = json.loads(JOURNAL.read_text(encoding="utf-8")) or None
        except json.JSONDecodeError as exc:
            return UNREADABLE, [f"journal is not readable JSON: {exc}"]

    if action == "open":
        if entry is not None:
            return CONFLICT, [
                ("an entry is already open; one experiment at a time. "
                 f"open paths: {entry.get('paths')}")
            ]
        baselines = {}
        for rel in paths:
            probe = _git("rev-parse", f"{SNAPSHOT}:{rel}")
            if probe.returncode != 0:
                return UNREADABLE, [f"{rel} does not exist at {SNAPSHOT[:7]}"]
            sha = probe.stdout.strip()
            live = _git("hash-object", rel).stdout.strip()
            if live != sha:
                return CONFLICT, [
                    (f"{rel} already differs from the snapshot before the experiment "
                     f"begins ({live[:7]} vs {sha[:7]}). Refusing to open: an entry "
                     "whose baseline is not the snapshot cannot authorise recovery.")
                ]
            baselines[rel] = sha
        JOURNAL.write_text(
            json.dumps({"snapshot": SNAPSHOT, "paths": list(paths),
                        "baselines": baselines, "note": note},
                       ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
        return OPEN, [f"opened over {len(paths)} path(s): {', '.join(paths)}"]

    if action == "close":
        if entry is None:
            return CLEAN, ["no entry to close"]
        wrong = [rel for rel, sha in entry["baselines"].items()
                 if _git("hash-object", rel).stdout.strip() != sha]
        if wrong:
            return CONFLICT, [
                f"refusing to close: {rel} does not match its baseline" for rel in wrong
            ]
        JOURNAL.unlink()
        return CLEAN, ["closed; every path matches its snapshot baseline"]

    # Restore only journaled paths (baseline = snapshot hash); other drift is reported, kept.
    if action == "recover":
        lines: list[str] = []
        if entry is not None:
            for rel, sha in entry["baselines"].items():
                _git("checkout", entry["snapshot"], "--", rel)
                now = _git("hash-object", rel).stdout.strip()
                lines.append(
                    f"{'restored' if now == sha else 'FAILED'} {rel} "
                    f"({now[:7]} vs baseline {sha[:7]})")
            if all(line.startswith("restored") for line in lines):
                JOURNAL.unlink()
        recorded = set(entry["paths"]) if entry else set()
        drifted = [p for p in _git("diff", "--name-only", SNAPSHOT, "--",
                                   *SUBJECT_PATHS).stdout.splitlines() if p.strip()]
        untracked = [p for p in _git("ls-files", "--others", "--exclude-standard", "--",
                                     *SUBJECT_PATHS).stdout.splitlines() if p.strip()]
        unexplained = [p for p in drifted + untracked if p not in recorded]
        if unexplained:
            lines += [f"INTEGRITY CONFLICT, left untouched: {p}" for p in unexplained]
            lines.append(
                "unexplained subject drift is not attributable to the journal and is "
                "NOT destroyed. Adjudicate it before continuing the program.")
            return CONFLICT, lines
        return (RESTORED if entry is not None else CLEAN), (lines or ["nothing to recover"])

    if entry is None:
        return CLEAN, ["no open entry"]
    return OPEN, [f"open over {entry['paths']}: {entry.get('note', '')}"]


_NAMES = {CLEAN: "CLEAN", OPEN: "OPEN", RESTORED: "RESTORED",
          CONFLICT: "CONFLICT", UNREADABLE: "UNREADABLE"}

if __name__ == "__main__":
    _action = sys.argv[1] if len(sys.argv) > 1 else "status"
    _paths = tuple(a for a in sys.argv[2:] if not a.startswith("--"))
    _note = next((a[7:] for a in sys.argv[2:] if a.startswith("--note=")), "")
    _code, _lines = journal(_action, _paths, _note)
    for _line in _lines:
        print(_line)
    print(f"QM-JOURNAL: {_NAMES.get(_code, 'UNKNOWN')} exit={_code}")
    raise SystemExit(_code)
