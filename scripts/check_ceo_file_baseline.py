"""CEO tier-0 file line counts vs soft ceilings.

See knowledge/doctrine/CENTRAL_AGENT_GOVERNANCE.md. Read-only; does not
modify the repo.
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
    "agent_tick.py": 1629,                 # +49 (2026-08-22, ревизия закрытий): _provider_health_line — оператор впервые видит, какого провайдера роутер пропускает. Найдено НЕ новым дефектом, а проверкой закрытия MIR-132 против названных полем провалов этого класса: «выключатель без наблюдаемости не настраивается» — наш был невидим. Первый черновик строки печатал «anthropic ok» провайдеру с 391 отказом по балансу (устаревшие отказы выпадают из окна остывания), поэтому строка показывает последний исход, а не только состояние остывания.  # +31 (2026-08-22, вторая правка дня): _sweep_episodic_duplicates — ровно ОДИН из тринадцати CLI-only органов MIR-131 переведён на безнадзорный путь, и граница проведена по суждению: схлопывание байт-идентичных дублей механично (новейший из одинаковых, потерь нет), а чистка по «ценности» — это суждение о собственной памяти (опасность кресла-резолвера, MIR-128) и остаётся оператору. Счёт класса: 43 одинаковых эпизода за один безнадзорный день.  # +8 (2026-08-22): починка застрявших строк очереди. Строка, брошенная умершим процессом, и строка, припаркованная исчерпанным бюджетом, — два состояния покоя с одним договором (общий замок, только старт), и второе не имело автоматического выхода вовсе: 14 строк, старейшая с 2026-07-30. Оба прохода уехали в _free_stranded_rows, поэтому run_tick НЕ вырос, а сократился 497 → 452 (ветвей 35 → 33, операторов 167 → 162); рост файла — цена выноса, а не длины функции.  # 1540 → 1541 (2026-08-19): докстринг честно называет реальный дефолт провайдера (аудит перед пушем).  # 1528 → 1540 (2026-08-19, вскрытие 19:31): _ensure_env_loaded — плановые тики выбирали цель до загрузки .env, то есть на модели по умолчанию.  # 1525 → 1528: леджер на чартерный путь — скрытый расход Sol стал видимым.  # measured 1522 (2026-08-15: --charter
    # flag + charter goal resolution — the agent picks its own campaign goal,
    # operator decision "строй хартию"); aspiration 1300 stands: the resolver
    # body lives in core/charter_goal.py, only the wiring is here
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
    "core/autonomous_runtime.py": 1084,  # +12 (2026-08-22): _config_from_task принимает resume_checkpoint. Отказ по виду задачи был не стражем, а тупиком: app/budget_guard пишет такую строку ИМЕННО ради безнадзорного пути, а безнадзорный путь на ней падал — 14 строк накопились и ни одна не была даже попробована. Пояснение вынесено в docs/CODE_NOTES.md, здесь остался контракт в три строки.  # −435 (2026-08-22, второй раскол): кластер предложений и self-build уехал миксином в core/autonomous_runtime_proposals — тела перенесены дословно, что пинится AST-сверкой с историей в tests/test_autonomous_runtime_proposals_split.py. Класс AutonomousRuntime: 1251 → 865 строк. Цель файла 1150 наконец достигнута.  # −161 (2026-08-22): шесть классов-данных и три алиаса уехали в core/autonomous_runtime_types по доводу оператора — файл, который нельзя прочесть целиком, порождает ошибки у всех троих читателей: у него, у меня и у самого агента, читающего свой исходник. Реэкспорт сохранён, интерфейс не менялся. Остаток честен: класс AutonomousRuntime — 1251 строка, то есть 83% файла; цель 1150 по-прежнему недостигнута, и разбор самого класса остаётся отдельной работой.  # +13 (2026-08-22): норма А, ратифицированная оператором — отчёт очереди перестал выдавать «очередь досушена» за «работа сделана». Четыре производных свойства и две колонки в записи, выведенные из статусов, которые отчёт УЖЕ нёс; MIR-117, живой случай — кампания с отказанной по бюджету задачей записала completed и полезный цикл. Рост — сама починка.  # +40 (2026-08-16, вторая правка дня): узкая разблокировка веба целевому пути (_goal_block_set/_UNBLOCKABLE_TOOLS) — учебное действие под стоячим грантом читает интернет; ничего кроме web_search/web_fetch этим полем не открывается.  # +84 (2026-08-16): стоячий грант — одно «да» на неделю питает автомат в стенах (дневной лимит, срок, журнал потребления data/standing_grant_usage.jsonl); решение оператора «строй автомат», см. CODE_NOTES «One yes a week».  # +4 (2026-08-16): python_probe в чёрном списке безнадзорного пути — лаборатория исполняет код, unattended остаётся repo-local (одни ворота за раз).  # +27 (2026-08-15): затвор эффектов ЧИТАЕТ ответ человека. До этого `effects_approved` ставился ровно в одном месте (`:approval-run`), кампания туда не ходит — она заводила заявку, блокировалась, и следующий прогон заводил новую с тем же dedup_key. Одобрение было письмом в никуда: живой круг — одобрено ain_369d8fdb, прогон встал на ain_bea806cc. Разрешение осталось одноразовым (executed), право §9 у человека (docs/CODE_NOTES.md «The approval nobody read»).    # measured 1495; aspiration 1150
    "core/smart_memory.py": 1940,  # +2 (2026-08-12): `content_refuted` вошёл в общий список дисквалификации — общий закон живёт в общем месте (docs/CODE_NOTES.md «REFUTED is a polarity»).  # +3 (2026-08-10): вердикт понижают ДВА сигнала — непредставленный контракт и неадресованные названные единицы.          # measured 1861 after the causal-credit split + outcome extraction +7 (2026-08-10): один предикат `_answer_disqualified` на ДВА рубежа — допуск эпизода и кредит процедуры. Порознь они уже разошлись, и самоопровергнувшийся прогон поднял счётчик активной процедуры. +12 (2026-08-10): вердикт завершения перестал удостоверять то, чего не проверял — `user_contract_partial` понижает заявленное `achieved`, как и `obligation_unmet`, и той же односторонней властью.  # 1924 -> 1940 (2026-08-15): третья ось допуска эпизода в опыт — объявление поля, проброс от вектора уверенности и сам затвор. Комментарии уехали в docs/CODE_NOTES.md, осталась механика; рост — сама починка, а не дрейф.
    "core/self_build_producer.py": 1857,   # 1841 → 1857 (2026-08-17): _python_body_vetoes + сито атрибутных фантомов в критике (meas_8fd5b4e5/be27ac23).  # 1860 → 1841: reply diagnosis moved out to core/builder_reply_diagnosis.py (MIR-084)
    "core/model_router.py": 1880,  # +8 (2026-08-16): выбор замены при failover рассказывает себя в route_reason (замер/разведка/пол + материал) — молчаливое решение охоты №9 нельзя было расследовать.          # 1860 → 1870 (2026-08-12, R7): declared-model resolution stopped letting builtin specs shadow the operator registry and started honouring AGENT_MODEL_MAX_COST, and a declared fallback now confesses itself in route_reason — probe_r1 ran entirely on builtin models while the configured one was advertised; the growth is the fix, not drift.  # 1800 → 1860: UsageTrackedLLM.stream_complete added (2026-08-08) — the streamed path used to escape billing via __getattr__; the billed method must live on the wrapper, so the growth is the fix, not drift
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
