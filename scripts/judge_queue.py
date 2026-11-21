"""Claude's judge queue (core/judge_queue.py): list pending items, rule on one, show agreement.

    python scripts/judge_queue.py [--root DIR] list
    python scripts/judge_queue.py [--root DIR] rule ID confirmed|overturned|unclear "what you saw"
    python scripts/judge_queue.py [--root DIR] stats

Rulings are written outside the agent's tools; this journal is closed to the agent.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from core.judge_queue import RULINGS, agreement, pending, rule


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--root", default=str(Path(__file__).resolve().parents[1]))
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list")
    sub.add_parser("stats")
    r = sub.add_parser("rule")
    r.add_argument("item_id")
    r.add_argument("verdict", choices=RULINGS)
    r.add_argument("reason")
    args = ap.parse_args(argv)
    if args.cmd == "list":
        items = pending(args.root)
        for item in items:
            print(json.dumps(item, ensure_ascii=False, indent=1))
        print(f"pending: {len(items)}")
    elif args.cmd == "stats":
        print(json.dumps(agreement(args.root), ensure_ascii=False, indent=1))
    else:
        print(json.dumps(rule(args.root, args.item_id, args.verdict, args.reason), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
