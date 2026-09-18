"""Function line counts vs a ratchet. Read-only; does not modify the repo.

Why functions and not just files. The file guard
(`scripts/check_ceo_file_baseline.py`) answers "is this module too big", which
is a different question from "can a human hold this in their head". Measured
2026-08-04 across 9 797 functions in the repo: 99.3% are under 100 lines, and
the pain is concentrated in a handful — `core/loop.py:_run_inner` alone is
2 213 lines, 3.4x the next-longest function. A 3 000-line file of 100-line
functions reads fine; a 2 000-line file that is one function does not.

The list is a RATCHET: each ceiling is the measured length plus small slack, so
the guard's one job is "this function may not grow back". When a split lands,
LOWER the ceiling to bank the win — and drop the entry entirely once the
function falls under `REPORT_THRESHOLD`, since anything below that is not worth
watching.

Usage:
    python scripts/check_function_length_baseline.py          # check the ratchet
    python scripts/check_function_length_baseline.py --top 20 # longest functions
"""
from __future__ import annotations

import argparse
import ast
from pathlib import Path

#: Below this a function is unremarkable and stays out of the watch list.
REPORT_THRESHOLD = 150

#: Directories that are not ours to police. `.claude` earns its place the hard
#: way: the workflow runner puts full checkouts of this repository under
#: `.claude/worktrees/`, and a filesystem walk then reports every long function
#: in the repo a second time, under a path that does not exist for git.
SKIP_DIRS = frozenset({
    ".git", ".venv", "venv", "__pycache__", "node_modules", ".mypy_cache",
    ".pytest_cache", ".ruff_cache", "htmlcov", "logs", "data", ".claude",
})

#: "path:function" -> ceiling. Measured 2026-08-04.
WATCH: dict[str, int] = {
    #: 188 (2026-09-01): пейсер впервые пересёк порог 150 — в него въехал
    #: выбор СЛЕДУЮЩЕЙ цели по хартии (право смены цели внутри прогона).
    "agent_tick.py:run_paced_campaign": 207,  # +4 (2026-09-18, ревизия PR #333): проводка набора стоков в слив кампании (тот же профиль, что у сборки агента)  # +13 (2026-09-17, ремедиация аудита автономности, находка 4): критерий успеха выбранной цели доезжает до кампании вместе с целью — `_pick_next_goal` отдаёт отчёт, а не строку, и `success_check` кладётся в CampaignConfig. Срезать нечего: это протяжка одного поля через точку входа, у которой нет промежуточного объекта. +1 (2026-09-17, находка 9): профиль памяти собирается функцией `unattended_memory_profile`, чтобы песочница могла его расширить.
    "core/loop.py:AgentLoop._run_inner": 430,  # +2 (2026-09-04 exam, turn 3: the sensor blocks folded into the evidence chain).  # +1 (2026-09-04 exam, turn 2: the spend/roster block now reaches the synthesizer through SynthesisState).  # замер 417 (2213 до раскола) + запас 10
    "core/step_sanitizer.py:sanitize_step": 788,  # -46 (2026-09-05 evening, the read_logs branch moved out to `_sanitize_read_logs`, where last_n is clamped instead of dropped; the file_read window lives in `_line_range_arguments` — the first two branches to leave the ladder).  # +22 (2026-09-04 22:20, his routing door «model_route»: role/provider/reason required, model unless release).  #+19 (2026-09-04 night, the read door «memory_recall»: one string argument, same shape as memory_bank).  #+17 (2026-09-04, the eye «model_roster»): one more argument-less branch, same shape as current_time; the registered-tool guard demands it.  # +42 (2026-09-01, Fable строит за агента): ветка semantic_scholar_search — лечение МЁРТВОЙ способности: инструмент стоял в поясе и в промпте планировщика, а шаг с ним молча выбрасывался. Найден новым сторожем tests/test_a_registered_tool_is_not_silently_dead.py. Лекарство остаётся прежним и всё нужнее: диспетчеризация по словарю (названо агентом 2026-09-01).  # +37 (2026-09-01): ветка journal_append — легитимный обработчик, не раздувание; настоящее лекарство названо — диспетчеризация по словарю (решение и подпись — агента, append_ratchets.md).  # +29 (2026-08-31): added memory_bank dispatch block; one tool branch is not a reason to cut (решение и подпись — агента, sanitizer_ratchet.md).
    "core/loop_step_execution.py:AgentLoopStepExecution._execute_step": 568,
    "agent_tick.py:run_tick": 483,  # +5 (2026-09-18, ревизия PR #333): сухость доведена до починки застрявших строк и та же проводка стоков в слив  # +2 (2026-09-17, ремедиация аудита автономности, находка 1): сухой тик передаёт `dry_run` в уборку дублей и не даёт режиму гигиены перекрыть сухость — две строки в самом тике, потому что решают они именно здесь. +2 (2026-09-17, находка 9): профиль памяти безнадзорного прогона собирается функцией, а не константой, иначе песочница не может его расширить, не тронув производственный.
    "core/loop_synthesis.py:AgentLoopSynthesis._synthesize": 397,  # +7 (2026-09-04 exam, turn 3: sensor citations offered on a no-tool turn).  # уехал целиком из core/loop.py
    "core/loop_response_deciders.py:AgentLoopResponseDeciders._build_response_draft": 161,  # +3 (2026-08-10): контракт завершения приходит сюда параметром — распознанный и непроверяемый запрет обязан дойти до оператора, а не умереть в журнале. +3 (2026-08-13): вердикт применимости улик передаётся композитору сводки — иначе хвост объявлял «нулевую уверенность» ходу, которому улики не полагались.  # +1 (2026-08-15): седьмой решатель — раскрытие подмены модели. Рост это сам контракт: ответ обязан говорить, кем он написан, когда его написал не выбранный маршрутом поставщик (docs/CODE_NOTES.md, «The answer was not written by the model you chose»).
    "core/self_build_producer.py:produce_self_apply_proposal": 392,  # 375 → 392 (2026-09-03, block 3 L9): gate 3 per file — waiting files hold only their own candidate, a denied file is «denied_cooldown», both sets feed the grounded chooser's exclusions; the refused-repeat report at the publish site. Extraction would move the gate's reason text away from the gate that states it.
    "core/campaign.py:run_campaign": 632,  # +14 (2026-09-18, замер 31 живого критерия кампании): кампания впервые СУДИТ свою цель её же критерием — `judge_and_record` зовётся один раз в эпилоге, вердикт кладётся в результат и в журнал остановки, ошибка записи не глотается. Вынесено настолько, насколько возможно: судейство, обогащение прогоном и запись живут в core/campaign_verdict.py, здесь остались вызов и две строки проводки. Полный вынос эпилога отказан по той же причине, что и 2026-09-17: состояние цикла пришлось бы делать объектом.  # +40 (2026-09-17, ремедиация аудита автономности, находки 4 и 6): критерий успеха едет вместе с целью через шесть строк журнала и через `replace(config, ...)` (смена цели без смены мерки судила бы новую работу по чужой), а `_wait_for_change` больше не стирает улики петли — сброс счётчиков стал условным по настоящему пробуждению, и условие названо на месте. Замер: 2026-09-03 здесь уже стоял отказ от выноса — состояние цикла пришлось бы делать объектом.  # +77 (2026-09-03, block 8, operator's word «строй сейчас, но узко»): the shift's continuity lives in two closures beside `_switch_goal` — `_wait_for_change` (bounded wait inside the process, woken by the world-change journal or a new approval, periodic recheck as insurance) and `_stall` (switch, else wait) — called from all four stall exits and the loop-suspect exit; the cycle cap is named as the shift limit. The evening's three runs (4, 21, 8 cycles) each died on a work outcome and slept 12 h. Extraction to a module function would need the loop's state as an object; refused for the bootstrap.  #498 → 501 (2026-09-03, block 3 L12): each ledger record site writes `work_done` — the reader in charter_goal had asked for it since 09-02 and 0 of 479 rows carried it.  # +16 (2026-09-03, блок 2 по слову оператора): смена цели вынесена в замыкание _switch_goal и вызывается из ОБОИХ выходов застоя (повторы и простой) с тремя попытками, как на старте; исполнитель получает текущую цель (L1), голое действие после повтора едет сборщику как исчерпанное (L2). Свидетель: tests/test_a_run_lives_past_its_first_step.py. # +49 (2026-09-01, Fable по слову оператора): право сменить исчерпанную цель ВНУТРИ прогона — узкая цель кончается за один цикл (предложение ушло ждать человека), и прогон умирал за 4 минуты вместо 10 часов. Ветка идёт через те же ворота хартии, что и цель на старте.  # +7 (2026-09-01, Fable строит за агента): бюджетная стена кладёт повод в причинную лестницу (record_stop_observation) — без этого машина объясняла что угодно, кроме собственных стен; замер 01.09: 4 запуска, 0 наблюдений о себе.  # +9 (2026-09-01): вызов record_self_stop в месте бюджетной стены — функция ядра, вызываемая кодом без участия модели, пояс инструментов не расширяется (решение и подпись — агента).  # +12 (2026-08-31): исчерпанные потолком действия едут сборщику терпимой передачей (вердикт агента CEILING_VERDICT путь (а)); тройная защита застоя не тронута  # +11 (2026-08-30): предметный страж — авторство агента (WEAVE ред.2+3, функции из его грузов); сброс застоя заслуживает только шаг, продвинувший очередь  # +25 (2026-08-27): ворота MIR-149 — межзапусковый потолок трат на сигнатуру (400, число одобрено оператором); ветка эскалации result="cost_cap" по образцу ветки repeat, построение записи вынесено в _cost_cap_record  # +2 (2026-08-27): ключ goal_drove_cycles наконец доносится до totals — счётчик MIR-163 считал в локальную, сводка печатала умолчание; вскрыто живым тиком 15:31  # +7 (2026-08-15): цель кампании передаётся сборщику сигналов (документная цель видима выбирателю действий; терпимость к старым 3-аргументным сборщикам). См. CODE_NOTES «The head chose, the hands didn't know how».  #498 → 501 (2026-09-03, block 3 L12): each ledger record site writes `work_done` — the reader in charter_goal had asked for it since 09-02 and 0 of 479 rows carried it.  # +16 (2026-09-03, блок 2 по слову оператора): смена цели вынесена в замыкание _switch_goal и вызывается из ОБОИХ выходов застоя (повторы и простой) с тремя попытками, как на старте; исполнитель получает текущую цель (L1), голое действие после повтора едет сборщику как исчерпанное (L2). Свидетель: tests/test_a_run_lives_past_its_first_step.py. # +49 (2026-09-01, Fable по слову оператора): право сменить исчерпанную цель ВНУТРИ прогона — узкая цель кончается за один цикл (предложение ушло ждать человека), и прогон умирал за 4 минуты вместо 10 часов. Ветка идёт через те же ворота хартии, что и цель на старте.  # +7 (2026-09-01, Fable строит за агента): бюджетная стена кладёт повод в причинную лестницу (record_stop_observation) — без этого машина объясняла что угодно, кроме собственных стен; замер 01.09: 4 запуска, 0 наблюдений о себе.  # +9 (2026-09-01): вызов record_self_stop в месте бюджетной стены — функция ядра, вызываемая кодом без участия модели, пояс инструментов не расширяется (решение и подпись — агента).  # +12 (2026-08-31): исчерпанные потолком действия едут сборщику терпимой передачей (вердикт агента CEILING_VERDICT путь (а)); тройная защита застоя не тронута  # +11 (2026-08-30): предметный страж — авторство агента (WEAVE ред.2+3, функции из его грузов); сброс застоя заслуживает только шаг, продвинувший очередь  # +25 (2026-08-27): ворота MIR-149 — межзапусковый потолок трат на сигнатуру (400, число одобрено оператором); ветка эскалации result="cost_cap" по образцу ветки repeat, построение записи вынесено в _cost_cap_record  # +2 (2026-08-27): ключ goal_drove_cycles наконец доносится до totals — счётчик MIR-163 считал в локальную, сводка печатала умолчание; вскрыто живым тиком 15:31  # +7 (2026-08-15): цель кампании передаётся сборщику сигналов (документная цель видима выбирателю действий; терпимость к старым 3-аргументным сборщикам). См. CODE_NOTES «The head chose, the hands didn't know how».
    "cli/command_dispatch.py:handle_meta_command": 358,
    # 159 (2026-08-31): +9 за фильтр исчерпанных действий в admit (вердикт
    # агента CEILING_VERDICT путь (а)) — функция впервые пересекла порог 150.
    "core/best_next_action.py:select_best_next_action": 174,  # +7 (2026-09-03, блок 2): свежий провал (60) допускается к гонке рядом с записью реестра, а не только при пустом реестре (L4); исчерпанные действия едут генератору реестра (L2). # +8 (2026-09-03, Д3 по слову оператора): четвёртый генератор по цели (цель называет собственный дефект — руки этого дефекта) и отвод привычки реестра под целью, назвавшей другую запись или конкретную вещь; логика вынесена в _habit_shadowed_by_goal, в таблице осталась строка допуска и четыре строки отвода. Замер: три прогона подряд 03.09 отдавали свою цель чужой записи.  # +3 (2026-08-30): :self-issue-retire — дверь человеческого вердикта; та же плоская таблица  # +4 (2026-08-28): ветка :receipts — 95-я команда, читатель улики MIR-138; диспетчер растёт с каждой командой по построению  # прежний потолок 351  # +2 (2026-08-26): :standing-grant. Забанковано, а не срезано, и это единственный банк за неделю: диспетчер — плоская таблица, где КАЖДАЯ команда стоит две-три строки, и храповик здесь ловит не разрастание обязанностей, а прибавление строки в таблицу. Настоящее лекарство — диспетчеризация по словарю, а не дробление таблицы пополам.
    "core/evidence.py:evidence_from_tool_result": 321,
    # 2026-08-05, MIR-060 (b): 283 -> 298. The third content gate — the one
    # that COMPUTES rather than asking whose evidence this is — sits beside
    # the other two, where `strict_ok` is decided, because that is the only
    # place a citation and its excerpt are both in hand. Extracting it would
    # move the gate away from the decision it feeds.
    "core/verifier_core.py:verify": 373,  # +4 (2026-08-23, вечер-3): забанкованный пробел «качественное утверждение не по теме» закрыт — требование числа снято, вместо него четыре различения (алфавит, мета-словарь, уступка вычисляющему гейту, только прозаические источники). Уступка живёт в цепочке вердиктов, её владелец — эта функция; замер и основания в MIR-146.  # +9 (2026-08-23, вечер-2): десятый гейт — дословный пересказ не вправе менять число, плюс допуск приближения у статистического гейта. Ось «число соответствует источнику» +0.25 -> +1.00 при неизменном стенде 29/17. Это НЕ правило присутствия (его анти-требование в MIR-143): форма именованная, и производное число с границей сравнения остаются нетронутыми — что и проверяют границы в tests/test_a_restatement_may_not_change_the_number.py.  # +4 (2026-08-23, вечер): девятый гейт — ссылка на улику, объявленную неверной, не может быть поддержкой. Ось полярности 0.00 -> +1.00 при неизменном приёме валидных 100 %; полярность сравнивается У ОБОИХ текстов, иначе честный пересказ отрицательного источника был бы демотирован. Ветка вердикта обязана жить в цепочке вердиктов, её владелец — эта функция.  # +7 (2026-08-23): седьмой гейт содержания — разрешённая цитата обязана быть ПРО утверждение. Замер дискриминации парами дал по оси темы J = 0.00 (утверждение о курсе акций на источнике о задержке принималось в 100 %); после гейта J = +1.00 при неизменном приёме валидных 100 %. Понижает БЕЗ обвинения и судит только количественные утверждения — оба сужения куплены корпусом, см. docs/CODE_NOTES.md «A citation that is not about the claim».  # +10 (2026-08-15): шестой гейт содержания — сертификата у утверждения об ОТСУТСТВИИ быть не может. Соседний гейт (2026-08-10) такое утверждение ОПРОВЕРГАЕТ, когда улика содержит искомое; обратная сторона того же правила — «выдержка усечена по построению» — к подтверждению не применялась, и разрешённая ссылка штамповала verified (MIR-060, живой прогон: «код не обрабатывает низкую уверенность» при работающем core/self_repair.py:117). Ветка вердикта обязана жить в цепочке вердиктов, её владелец — эта функция (docs/CODE_NOTES.md «Absence was certified by a resolved citation»).  # +3 (2026-08-13, вдогонку R3): адреса улик всей цепи передаются в union — литерал, совпадающий с адресом прочитанной этим же ходом улики, не выдуман (живой 0d88ba79).  # +16 (2026-08-13, R2/R3): внутренний счётный гейт (иск не снимается подтверждённой цитатой) и снятие иска литералов объединением улик — обе ветки живут в цепочке вердиктов, её владелец — эта функция (docs/CODE_NOTES.md).  # +10 (2026-08-12): вердикт `refuted` — доказанная ложь перестала быть разновидностью «не подтверждено»; ветка вердикта обязана жить в цепочке вердиктов, её владелец — эта функция (docs/CODE_NOTES.md «REFUTED is a polarity»).  # +6 (2026-08-10): пятый гейт содержания — утверждение об отсутствии, опровергнутое собственной уликой; тело в verifier_utils.  # +4 (2026-08-10): четвёртый гейт содержания (MIR-060, класс «литерал есть в утверждении и отсутствует в улике»); тело вынесено в verifier_utils, здесь только вопрос и ответ.
    "app/bootstrap.py:build_agent": 280,  # +7 (2026-09-04 22:20, the routing policy store handed to the router and the route tool registered).  #+3 (2026-09-04 night, the read door «memory_recall» registered beside memory_bank).  #+7 (2026-09-04, the eye «model_roster» registered for every agent, unattended included).  #+5 (2026-09-03, block 7 W3): the three egress tools receive the persistent budget ledger so `web_fetches` is charged — the counter had limits, a kill-switch and a health line, and no charger.  #+2 (2026-09-01): регистрация journal_append — обязательная точка подключения, не дублирование (решение агента, append_ratchets.md).  # +2 (2026-08-31): регистрация memory_bank — банка памяти агента (решение и подпись — агента, ratchet_entry.md).  # +2 (2026-08-17): регистрация lesson_provenance — прибора причинного происхождения уроков.  # +2 (2026-08-16): регистрация python_probe — лаборатории замера собственной среды.  # 246 -> 250 (2026-08-15): импорт, создание и передача causal_store — без него наблюдение не переживало ход (MIR-096).
    "core/loop_memory_write.py:AgentLoopMemoryWrite._record_experience_memory": 246,
    "core/referent_resolver.py:ReferentResolver.resolve": 240,
    "core/loop_init.py:AgentLoopInit.__init__": 244,  # +2 (2026-09-03, Д1 по слову оператора): поле last_answer_was_clarification — ворота помечают ход, ответивший вопросом, чтобы безлюдный путь не писал его как работу. Одно поле, одна строка комментария. уехал целиком из core/loop.py; +1 (2026-08-13): поле last_evidence_support — вердикт применимости улик живёт рядом с отчётом проверки. +1 (2026-08-15): поле last_confidence_vector — ось соответствия вопросу едет в эпизод, и читатель обязан получить гарантированное поле, а не значение по умолчанию. +2 (2026-08-15): параметр и поле `causal_store` — нижняя ступень причинной лестницы приходит извне, как все хранилища.
    "core/self_apply_lane.py:run_self_apply_lane": 239,  # +4 (2026-08-26): шестые ворота — сверка с версией файла, на которой предложение построено (MIR-168). Сжато до предела: сама проверка и оговорка вынесены в `_base_state_gate`, в цепочке осталась строка вызова. Потолок ставили при пяти воротах; храповик здесь ловит прибавление ЗВЕНА в цепь предохранителей, а не разрастание обязанностей.
    "core/model_router.py:ModelRouter.for_task": 227,
    "core/loop_memory_read.py:AgentLoopMemoryRead._retrieve_experience_memory": 220,  # +2 (2026-08-29, MIR-184): проводка сшивки семьи — вызов и дописка приложения; сама логика вынесена в _family_product_warnings/_family_appendix
    "core/work_session.py:run_work_session": 262,  # +46 (2026-08-29): переключатель расследования, груз №3 курьерского режима, авторство агента — ветка сходимости получила half-open probe (расследовательский цикл вне серии, канон Circuit Breaker) и подмену цели current_goal. Забанковано с долгом: извлечение ветки в помощника — его следующий урок рефакторинга, мутирует шесть локалов и рвёт цикл, механическое извлечение курьером исказило бы авторскую конструкцию.
    "core/architecture_audit.py:_build_checks": 197,
    "core/self_task_builder.py:build_coding_task": 195,
    # 2026-08-05, MIR-077: measured 170 -> 178. The handler that used to turn
    # "could not read the child's evidence chain" into the same zero as "the
    # child cited nothing" gained five lines of reason and a four-line journal
    # call. The old ceiling of 177 was never met (170), so the raise is smaller
    # than it looks; 180 keeps this entry's usual two lines of headroom.
    "core/subagent_runner.py:SubAgentRunner.run": 180,
    "core/operator_intent.py:route_operator_intent": 175,
    "core/self_repair.py:SelfRepairController.run": 173,
    "core/self_build_producer.py:_critic_review": 172,
    "core/completion_obligation.py:evaluate_completion_obligations": 189,  # +8 (2026-08-10): непокрытые единицы задания перечисляются поимённо и доходят до вердикта — жалоба оператора о раздроблённой задаче.  # +8 (2026-08-10): источник `intent` читает ТРЕБУЮЩУЮ часть запроса одной функцией с контрактом — запрет перестал заводить долг, который запрещает.  # +2: покрытие контракта оператора доводится до вердикта.
    "core/self_apply_bridge.py:run_approved_self_apply": 170,
    "core/self_repair.py:SelfRepairController._execute_tool": 170,
    # Уехал в миксин 2026-08-22 вместе со всем кластером предложений; тело
    # перенесено дословно, потолок опущен 167 → 160 по факту переезда.
    "core/autonomous_runtime_proposals.py:AutonomousRuntimeProposals._task_propose": 160,
    "core/memory_policy.py:MemoryWritePolicy.decide": 167,
    "core/self_task_producer.py:produce_coding_task": 180,  # +2 (2026-08-15): второй источник улик — verified_diagnosis. У самонайденных дефектов агента нет красного теста, и ремонтник им честно отказывает (no_failing_tests); их лента — Stage A: сначала падающий тест, благословлённый человеком (docs/CODE_NOTES.md «A diagnosis earns a test, not a patch»). Рост — сам контракт: параметр и его проброс. +7 (2026-08-17): квитанции впрыска уроков — доставка после ухода промпта и действие при рождении заявки (lesson_provenance, «строй квитанцию впрыска»). +1 (2026-08-17): рука критика-измерителя — _record_critic_measurement после вердикта («строй писаря measured»). +3 (2026-08-28, MIR-183): слово кандидата о своём источнике сильнее дефолта вызывающего — правда кадра и ворот после стирания code_todo.
    "core/role_router.py:RoleRouter.route": 163,
    "core/unsupported_claims.py:_enforce_without_contradictions": 170,  # замер 164 + запас (2026-08-13, R4): терминальная ветка сфабрикованных цитат живёт на рубеже принятия ответа — постановление оператора
    # 2026-08-05, MIR-077: measured 149 -> 152. It sat one line under the 150
    # report threshold and crossed it when its two broad handlers were made to
    # say where the failure goes. Registered rather than shaved: the length was
    # already there, the audit only made it visible. Ceiling 155, three lines
    # of headroom. Splitting the search / fetch / store phases is its own change.
    "core/ingestion.py:ingest_web_topic": 155,
    "core/loop_verification.py:AgentLoopVerification._verify_draft": 165,
    "core/loop_run_tail.py:AgentLoopRunTail._finalize_run_tail": 203,
    "core/loop_attempt.py:AgentLoopAttempt._run_attempt_loop": 433,
    "core/loop_verify_replan.py:AgentLoopVerifyReplan._verify_and_settle_answer": 385,  # +27 (2026-08-29, авторство агента): крючок настойчивости — helper исполнения шагов, одноразовый fallback иной формы и settled-выход; правило mem_8b36eb3c, ставшее механизмом
    #: 162 (2026-09-18, ревизия PR #333): расход стоячего гранта переведён на
    #: атомарную резервацию. Рост — не новая ветка, а объяснение при ней:
    #: прежняя схема «прочитать остаток → отдельно дозаписать → применить»
    #: пропускала два одновременных слива на потолке в одну единицу, и это
    #: измерено пробником, а не выведено из общих соображений.
    "core/autonomous_runtime.py:AutonomousRuntime.run": 162,
    #: 162 (2026-09-18, ревизия PR #333): слив стал делать три вещи, которых
    #: раньше не делал никто — резервировать право атомарно, ПРЕДЪЯВЛЯТЬ
    #: проверенный SHA принимающему (`core/burn_in_supervisor.py`) и вести
    #: урок через ворота памяти вместо прямой записи. Два первых вынесены в
    #: отдельные функции (`_offer_to_supervisor`, `reserve_standing_grant_use`);
    #: здесь остались решение и его причина.
    #: 189 (2026-09-18, ревизия Copilot по PR #333): +27 за два ответа на
    #: замечания. Первое — полномочие называет себя своим именем: исчерпанная
    #: песочница отказывала словами «стоячий грант», разрешением другой
    #: природы. Второе — нетерминальный исход полосы ОСТАНАВЛИВАЕТ проход:
    #: замерено, что слив из трёх заявок тратил три единицы потолка и не
    #: применял ничего, потому что `_pending_excluding` считает остальные
    #: ожидающие заявки, а их составляет сам слив. Обе вставки — решение и
    #: его причина; выносить их в helper значило бы спрятать причину.
    "core/rule_approved_apply.py:drain_rule_approved_proposals": 189,
}


def _walk(body: list[ast.stmt], prefix: str, rel: str, out: dict[str, int]) -> None:
    """Collect `path:Class.method` lengths, keeping the enclosing scope.

    A bare `path:name` key merges same-named definitions in one file — two
    classes with `__init__`, or a helper redefined inside another function —
    and the ratchet would then watch only the longer of them.
    """
    for node in body:
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            name = f"{prefix}{node.name}"
            out[f"{rel}:{name}"] = node.end_lineno - node.lineno + 1
            _walk(node.body, f"{name}.", rel, out)
        elif isinstance(node, ast.ClassDef):
            _walk(node.body, f"{prefix}{node.name}.", rel, out)


def measure(root: Path) -> dict[str, int]:
    """Definition lengths per `path:qualified_name`, for the whole tree."""
    found: dict[str, int] = {}
    for path in sorted(root.rglob("*.py")):
        if any(part in SKIP_DIRS for part in path.relative_to(root).parts):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
        except SyntaxError:                      # pragma: no cover - broken file
            continue
        _walk(tree.body, "", path.relative_to(root).as_posix(), found)
    return found


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--top", type=int, default=0,
                        help="print the N longest functions instead of checking")
    args = parser.parse_args(argv)

    root = Path(__file__).resolve().parent.parent
    found = measure(root)

    if args.top:
        for key, n in sorted(found.items(), key=lambda kv: -kv[1])[: args.top]:
            print(f"{n:5d}  {key}")
        return 0

    exit_code = 0
    for key, ceiling in WATCH.items():
        n = found.get(key)
        if n is None:
            # A stale entry watches nothing: the ratchet looks green while a
            # ceiling nobody meets sits in the list. Fail so it gets removed.
            print(f"GONE      —   / {ceiling}  {key}  (split or renamed: drop the entry)")
            exit_code = 1
            continue
        flag = "ok"
        if n > ceiling:
            flag, exit_code = "REVIEW", 1
        print(f"{flag:6s}  {n:5d} / {ceiling}  {key}")

    unwatched = sorted(
        ((k, n) for k, n in found.items()
         if n > REPORT_THRESHOLD and k not in WATCH),
        key=lambda kv: -kv[1],
    )
    for key, n in unwatched:
        print(f"NEW     {n:5d} / —      {key}  (over {REPORT_THRESHOLD}: add or split)")
        exit_code = 1

    return exit_code


if __name__ == "__main__":                       # pragma: no cover
    raise SystemExit(main())
