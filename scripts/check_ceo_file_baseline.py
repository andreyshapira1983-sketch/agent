"""CEO tier-0 file line counts vs soft ceilings.

See knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md. Read-only; does not modify the repo.

Usage:
    python scripts/check_ceo_file_baseline.py
"""
from __future__ import annotations

from pathlib import Path

# Ceilings are a RATCHET, not an aspiration: each is the measured size at the
# last review plus small slack, so the guard's one job is "this file may not
# grow back". When a decomposition lands (loop.py: 4733 -> 4047 via #217-#224),
# LOWER the ceiling to bank the win. Aspirational targets live in the comment
# column; reaching one is task-list work (planner.py is task #5), not this
# guard's business.
#
# Found orphaned by the 2026-08 audit: this script was wired into nothing, so
# three files sat over their ceilings with the guard reporting it to nobody.
# It now runs inside the test suite (tests/test_file_size_ratchet.py).
WATCH: dict[str, int] = {
    # 740 -> 744, 2026-08-09, ЧЕТЫРЕ строки на починку, признанную оператором:
    # уточняющий вопрос выбрасывал задачу — ответ оператора становился ВСЕМ
    # вопросом следующего прогона (замерено в продакшене дважды). Минимизировано
    # прежде подъёма: само возобновление уехало в `core/loop_gates.py`, сброс
    # вектора уверенности — в `core/loop_verification.py`, комментарии срезаны до
    # контракта в одну строку, пояснение живёт в docs/CODE_NOTES.md. Остаток —
    # вызов, флаг и запоминание вопроса; меньше не бывает без потери смысла.
    "core/loop.py": 761,                  # разбор на модули (правило оператора: потолок 2000): −828 строк ушло в core/loop_step_execution, −234 в core/loop_response_deciders, −423 в core/loop_synthesis, −106 в core/loop_evidence_chain, −123 в core/loop_verification, −122 в core/loop_observe, −165 в core/loop_run_tail, −57 и −63 в loop_evidence_chain/loop_context, −375 в core/loop_attempt, −341 в core/loop_verify_replan, −254 в core/loop_init, −84 в core/loop_synthesis, −158 мелких методов, −139 ворот в core/loop_gates, −97 пролога и обязательств; оркестратор +8 (2026-08-10): ребро происхождения прогона — обёртка `run` владеет идентичностью прогона, и записывать связь run_id/trace_id/session_id обязана она, а не сосед. +8 ещё: журнал без trace_id ребра не даёт, и это отсутствие названо явно (`run_identity_unavailable`) — иначе «связи нет» и «связь не записали» снова неразличимы. +1: контракт завершения передаётся в рубеж принятия ответа run-локалью.
    "main.py": 2000,                       # 47 today; the old extraction's win
    "core/planner.py": 560,                # measured 516 after piece 5 (host-tools context out)
    "agent_tick.py": 1500,                 # measured 1458; aspiration 1300
    # 2026-08-05, MIR-077: 1397 -> 1483. Ten broad handlers here were the
    # largest single concentration of the invisible-failure class; each now
    # journals its failure or says why silence is right. The file is further
    # from its 1150 aspiration and closer to being diagnosable — a trade taken
    # deliberately, not a drift. Splitting it is MIR-078.
    # (The 1364 in the previous note was the measurement of an older tree; the
    # baseline before this change was 1397 — checked against origin/main, not
    # recalled from the comment above it.)
    # 2026-08-05, второй раз за день: 1483 -> 1495. Флаг
    # `learning_writes_memory` и его обоснование — оператор не мог
    # включить долгую память в кампании, потому что `auto_write_memory`
    # был захардкожен. Девять строк комментария на девять строк кода:
    # решение «писать ли в постоянную память без присмотра» стоит того,
    # чтобы следующий читатель узнал причину, а не восстанавливал её.
    "core/autonomous_runtime.py": 1500,    # measured 1495; aspiration 1150
    "core/smart_memory.py": 1919,          # measured 1861 after the causal-credit split + outcome extraction +7 (2026-08-10): один предикат `_answer_disqualified` на ДВА рубежа — допуск эпизода и кредит процедуры. Порознь они уже разошлись, и самоопровергнувшийся прогон поднял счётчик активной процедуры. +12 (2026-08-10): вердикт завершения перестал удостоверять то, чего не проверял — `user_contract_partial` понижает заявленное `achieved`, как и `obligation_unmet`, и той же односторонней властью.
    "core/self_build_producer.py": 1841,   # 1860 → 1841: reply diagnosis moved out to core/builder_reply_diagnosis.py (MIR-084)
    "core/model_router.py": 1860,          # 1800 → 1860: UsageTrackedLLM.stream_complete added (2026-08-08) — the streamed path used to escape billing via __getattr__; the billed method must live on the wrapper, so the growth is the fix, not drift
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
