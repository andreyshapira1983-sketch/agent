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
    args = ap.parse_args(argv)

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
    worker = MarketWorker(client, lambda text: format_human_response(agent.run(user_question=text)),
                          allowed_jobs=set(args.allow_job), workdir=args.workspace / "data" / "market")
    while True:
        for rep in worker.poll_once():
            print(f"[{rep.kind}] {rep.assignment_id} job={rep.job_id}: {'; '.join(rep.steps)}"
                  + (f" | ОШИБКА {rep.error}" if rep.error else ""), flush=True)
        if args.once:
            return 0
        time.sleep(max(30.0, args.interval))


if __name__ == "__main__":
    sys.exit(main())
