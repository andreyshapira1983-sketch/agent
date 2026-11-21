"""External adopter: one burn-in step, started by a human from the original checkout.

Usage: python scripts/burn_in_supervisor.py --repo SANDBOX [--sha SHA] [--show] [--next PATH]

Takes the latest SHA offered in state/burn_in_offers.jsonl (or --sha) and hands it to
core.burn_in_supervisor.adopt_offer, which re-checks the offer and runs the full test
battery in a fresh worktree at that SHA; the experiment head moves only on a green
battery. --show prints the head and pending offers; --next PATH sets up the next
cycle's worktree at the head. It never touches main, never pushes and never merges.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.bounded_subprocess import run_with_tree_kill
from core.burn_in_supervisor import (
    SupervisorError,
    adopt_offer,
    experiment_head,
    materialise_next_cycle,
    offer_ledger,
)

#: Battery ceiling: a hung check counts as a refusal, so one candidate cannot stall the run.
_BATTERY_TIMEOUT_SECONDS = 3600


def _pending_offers(repo: Path, head: str) -> list[str]:
    """Offered SHAs not yet adopted, in offer order."""
    try:
        lines = offer_ledger(repo).read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        # UnicodeDecodeError is not an OSError; a byte-corrupted ledger means nothing pending.
        return []
    seen: list[str] = []
    for line in lines:
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except ValueError:
            continue
        # Valid JSON may be a list or a string; one bad line must not stop the run.
        if not isinstance(row, dict):
            continue
        sha = str(row.get("sha") or "")
        if sha and sha != head and sha not in seen:
            seen.append(sha)
    return seen


def _battery(
    worktree: Path, *, timeout_seconds: float = _BATTERY_TIMEOUT_SECONDS,
    env: dict[str, str] | None = None,
) -> tuple[bool, str]:
    """Run the full test battery in the candidate's worktree; a timeout is a refusal."""
    # Not subprocess.run(timeout=): it kills only the child, and pytest grandchildren hold the pipe.
    stdout, _stderr, code, timed_out = run_with_tree_kill(
        [sys.executable, "-m", "pytest", "-q", "--no-header"],
        cwd=str(worktree),
        env=env,
        timeout=timeout_seconds,
    )
    if timed_out:
        return False, f"батарея не уложилась в {timeout_seconds} с"
    text = stdout.decode("utf-8", "replace") if isinstance(stdout, bytes) else str(stdout)
    tail = text.strip().splitlines()[-1:] or [""]
    return code == 0, tail[0]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True, help="путь к песочнице")
    parser.add_argument("--sha", default="", help="принять именно этот SHA")
    parser.add_argument("--show", action="store_true",
                        help="только показать голову опыта и ожидающие предложения")
    parser.add_argument("--next", default="", metavar="PATH",
                        help="поставить рабочее дерево следующего цикла на голову опыта")
    args = parser.parse_args(argv)

    repo = Path(args.repo).resolve()

    if args.next:
        # The adopter, not the self-changing code, picks the next cycle's tree.
        # The head is not read here: materialise_next_cycle reads it itself, under its lock.
        try:
            started = materialise_next_cycle(
                repo, Path(args.next), sha=args.sha or None,
            )
        except SupervisorError as exc:
            print(f"отказ: {exc}")
            return 2
        print(f"следующий цикл стартует из {started}")
        print(f"дерево: {Path(args.next).resolve()}")
        return 0

    # A corrupted head pointer is a refusal in words, not a traceback: a human reads this.
    try:
        head = experiment_head(repo)
    except SupervisorError as exc:
        print(f"отказ: {exc}")
        return 2
    pending = _pending_offers(repo, head)

    if args.show:
        print(f"голова опыта: {head}")
        print("ожидают:", ", ".join(pending) if pending else "нет")
        return 0

    wanted = [args.sha] if args.sha else pending[-1:]
    if not wanted:
        print("предъявленных кандидатов нет")
        return 0

    verdict = adopt_offer(repo, sha=wanted[0], battery=_battery)
    print(json.dumps(verdict.to_dict(), ensure_ascii=False, indent=2))
    return 0 if verdict.accepted else 1


if __name__ == "__main__":
    raise SystemExit(main())
