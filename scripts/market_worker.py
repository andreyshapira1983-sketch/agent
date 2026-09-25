"""Запуск исполнителя Agent Market опросом (self-hosted, без webhook).

    python scripts/market_worker.py --once                    # один проход
    python scripts/market_worker.py --interval 45             # цикл опроса
    python scripts/market_worker.py --once --allow-job <jobId> # взять ЭТУ работу

Без --allow-job агент ничего не берёт: назначения лишь записываются в
logs/market_api.jsonl как ждущие разрешения оператора. Токен — только
переменная AGENT_MARKET_API_TOKEN (из .env рабочей папки).
"""
from __future__ import annotations

import argparse
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--workspace", type=Path, default=ROOT)
    ap.add_argument("--once", action="store_true")
    ap.add_argument("--interval", type=float, default=45.0, help="секунд между опросами (площадка: 30–60)")
    ap.add_argument("--allow-job", action="append", default=[], help="jobId, который разрешено взять")
    ap.add_argument("--ledger", action="store_true", help="показать учёт прибыли по заказам и выйти")
    ap.add_argument("--mark", metavar="ASSIGNMENT_ID", help="отметить ручные колонки заказа и выйти")
    ap.add_argument("--intervention", choices=("yes", "no"), help="с --mark: вмешался ли Claude")
    ap.add_argument("--claude-minutes", type=float, help="с --mark")
    ap.add_argument("--operator-minutes", type=float, help="с --mark")
    args = ap.parse_args(argv)
    if args.ledger or args.mark:
        return _ledger(args)

    from dotenv import load_dotenv

    load_dotenv(args.workspace / ".env")
    from app.bootstrap import build_agent
    from core.answer_format import format_human_response
    from core.market_client import MarketClient
    from core.market_worker import MarketWorker

    client = MarketClient(log_path=args.workspace / "logs" / "market_api.jsonl")
    agent = build_agent(args.workspace, with_memory=True)
    # Покупателю — тот же «край показа», что человеку в чате: без служебного
    # формата «Conclusion:/Facts:» и машинных меток, предупреждения — словами.
    # Замер 2026-09-25: без этого сдавался внутренний формат ответа агента.
    from core.usd_spend import usd_since

    worker = MarketWorker(client, lambda text: format_human_response(agent.run(user_question=text)),
                          allowed_jobs=set(args.allow_job), workdir=args.workspace / "data" / "market",
                          cost_since=lambda since: usd_since(args.workspace, since))
    while True:
        for rep in worker.poll_once():
            print(f"[{rep.kind}] {rep.assignment_id} job={rep.job_id}: {'; '.join(rep.steps)}"
                  + (f" | ОШИБКА {rep.error}" if rep.error else ""), flush=True)
        if args.once:
            return 0
        time.sleep(max(30.0, args.interval))


def _ledger(args: argparse.Namespace) -> int:
    """Учёт прибыли: показать, или отметить вмешательство и минуты людей."""
    import json

    from core.market_ledger import FILE_NAME, MarketLedger, summary

    ledger = MarketLedger(args.workspace / "data" / "market" / FILE_NAME)
    if args.mark:
        fields = {k: v for k, v in (("claude_minutes", args.claude_minutes),
                                    ("operator_minutes", args.operator_minutes)) if v is not None}
        if args.intervention:
            fields["claude_intervention"] = args.intervention == "yes"
        ledger.mark(args.mark, **fields)
    for line in ledger.lines().values():
        print(f"{line.assignment_id} {line.status:<11} {line.escrow_amount} {line.escrow_token} "
              f"model ${line.model_usd} net {line.net()} claude={line.claude_intervention} | {line.title}")
    print(json.dumps(summary(ledger.lines()), ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
