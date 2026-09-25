"""Отчёт мерила полезности (core/work_usefulness.py): только чтение.

    python scripts/usefulness_report.py                  # сводка по рабочей папке
    python scripts/usefulness_report.py --window 3 --examples 5
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", type=Path, default=ROOT)
    ap.add_argument("--window", type=int, default=3, help="дней после цикла, в которые ищется польза")
    ap.add_argument("--examples", type=int, default=3)
    args = ap.parse_args(argv)

    from core.work_usefulness import market_accepted, measure, summary

    cycles = measure(args.workspace, window_days=args.window)
    print(json.dumps(summary(cycles, market_accepted(args.workspace)), ensure_ascii=False, indent=1))
    for verdict in ("useful", "not_yet"):
        picked = [c for c in cycles if c.verdict == verdict][-args.examples:] if args.examples > 0 else []
        print(f"\n{verdict}:")
        for c in picked:
            on = ",".join(s for s, v in c.signals.items() if v) or "-"
            print(f"  {c.ts[:16]} [{on}] calls={c.llm_calls} products={sorted(c.products)[:2]} | {c.goal[:90]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
