"""Разовая миграция MIR-058: зачёт без выжившей улики теряет право голоса.

КОМУ ЭТО НУЖНО. Ворота MIR-057 остановили НОВЫЙ непроверяемый зачёт, но
только вперёд; уборка эпизодов о процедурах не знает (MIR-128), поэтому улика
выносится, а начисленная по ней уверенность её переживает. Живой пик:
`tools:file_read` — уверенность 0.962, active, 31 опора, все 31 отсутствуют.

ЧТО ДЕЛАЕТ, на процедуру: если статус `active`, зачёт ненулевой И НИ ОДНОГО
из `source_episode_ids` больше нет в эпизодическом хранилище — статус
становится `needs_review`. Фильтр подбора (`search_with_report`) такие не
подаёт планировщику. Счётчики и уверенность НЕ трогаются: журнал «было
начислено» честен; переписывать его значило бы выдумывать вердикт руками.
Обратимо: проверяемые завершения возвращают active обычным жизненным циклом.

БЕЗОПАСНОСТЬ. Dry-run по умолчанию; `--apply` пишет. Перед записью — копия
в семье имён `<file>.<ts>.bak`, которую узнаёт метла MIR-125 (закон
keep_last=3 хранит новейшие). Запуск:
    python scripts/demote_unverifiable_procedure_standing.py            # отчёт
    python scripts/demote_unverifiable_procedure_standing.py --apply    # запись
"""
from __future__ import annotations

import argparse
import shutil
import sys
from dataclasses import replace
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from core.smart_memory import (  # noqa: E402
    EpisodicMemoryStore,
    ProceduralMemoryStore,
)


def select_and_demote(
    pstore: ProceduralMemoryStore,
    estore: EpisodicMemoryStore,
    *,
    apply: bool,
) -> dict:
    """Отобрать и (при apply) перевести; отчёт в обоих режимах одинаков."""
    alive_ids = {ep.id for ep in estore.load()}
    procedures = pstore.load()
    demoted: list[str] = []
    out = []
    for proc in procedures:
        unverifiable = (
            proc.status == "active"
            and (proc.success_count or proc.failure_count)
            and proc.source_episode_ids
            and not any(ref in alive_ids for ref in proc.source_episode_ids)
        )
        if unverifiable:
            demoted.append(proc.id)
            out.append(replace(
                proc, status="needs_review",
                updated_at=datetime.now(timezone.utc).isoformat(),
            ))
        else:
            out.append(proc)
    if apply and demoted:
        pstore.rewrite(out)
    return {
        "scanned": len(procedures),
        "demoted": demoted,
        "applied": bool(apply and demoted),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--workspace", default=str(ROOT))
    args = parser.parse_args()

    ws = Path(args.workspace)
    ppath = ws / "data" / "procedural_memory.jsonl"
    pstore = ProceduralMemoryStore(ppath)
    estore = EpisodicMemoryStore(ws / "data" / "episodic_memory.jsonl")

    if args.apply and ppath.is_file():
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        shutil.copy2(ppath, ppath.with_name(f"{ppath.name}.{ts}.bak"))

    report = select_and_demote(pstore, estore, apply=args.apply)
    mode = "APPLIED" if report["applied"] else "DRY-RUN"
    print(f"[{mode}] scanned={report['scanned']} demoted={len(report['demoted'])}")
    for pid in report["demoted"]:
        print(f"  needs_review: {pid}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
