# Architectural freeze — the decision subject is wrong

**This is now the blocking architectural issue of the project. All other
development is subordinate to it.**

Declared by the operator on 2026-08-20. Nothing new is to be built — no new
features, subsystems, roles, agents, memory mechanisms, self-build capabilities,
planning mechanisms or organisational abstractions — until the architecture of
autonomy is corrected.

## Why

The system was designed as an autonomous agent. Over months of development a
large share of its **executive decisions** was written into the code by humans
and by LLM developers: which tasks to run, in what order, which roles exist,
what matters more than what, what to study first, when to improve itself. The
result is a mixture of autonomous decision-making and hidden scripted
behaviour.

Building further on that mixture only adds to what will have to be untangled.
A better memory would remember imposed decisions more accurately; better
subagents would execute an imposed organisation more efficiently; better
self-repair would more efficiently maintain a system still travelling an
imposed trajectory.

## The two invariants, and there are only two

Everything else in this document is evidence for these or consequence of them.
Kept deliberately short, because a freeze that grows a philosophy stops being
enforceable.

> **I. WHO DECIDES.** Executive decisions that belong to the autonomous agent
> must not be secretly pre-decided by a developer. Hardcoded code may define
> safety constraints and generic capabilities; it must not prescribe the
> director's workforce, task agenda, organisational topology or utility
> ranking. Every such choice must be traceable to the agent's own
> deliberation, evidence and retained experience.
>
> **II. WHO LIVES.** All production functions must belong to one canonical
> autonomous agent, not to several independently assembled cognitive
> instances. Restarting a process recovers that same agent rather than
> creating a new one.

### An anti-pattern to refuse in advance

**Do not solve lifecycle continuity by preserving a transient implementation
identifier.** `new_trace_id()` is defined as a random per-session trace
identifier and it SHOULD keep changing. Writing it to disk and calling identity
continuity fixed would produce a permanent journal number, not a continuing
life.

Two layers, and only the first is missing:

    Agent ID   — WHO lived. Not present anywhere today.
    Trace / run / cycle ID — WHAT it did at a given moment. Present, and must
                             go on changing so events of one life stay
                             distinguishable.

Establish first what must be continuous, then choose a representation. For a
first-level autonomous agent that does not require building a "digital
subject"; at minimum what has to survive is: this is the same agent, its
accumulated memory, its unfinished work, its past results, its permissions and
limits, and its accumulated experience.

### Equivalence is not continuity

`agent_tick.py` defines its memory profile once with the comment that all three
build sites must stay identical. That sentence is the defect stated plainly: it
maintains **equivalence between instances** because there is no single owner of
the configuration. A million identical instances do not add up to one
continuing agent.

## The first invariant in detail

> Hardcoded code may define safety constraints and generic capabilities, but
> must not prescribe the director's workforce, task agenda, organisational
> topology, or utility ranking. Every such choice must be traceable to the
> director's own deliberation, evidence and retained experience.

Three classes have been conflated in the code, and separating them is the work:

| Class | Who sets it | Example |
|---|---|---|
| **Constitution** | the human | kill switch, no self-issued credentials, budget ceiling, no widening its own rights |
| **Capability** | the human, in advance | a tool that can spawn a sandboxed worker, a memory API, a rollback mechanism |
| **Decision** | the agent | what matters now, which organisation to build, whom to hire or retire, what to study |

## Verified sites where code decides instead of the agent

Read from the code on 2026-08-20, not from a summary. Each line was confirmed
in the file named.

| Site | What the code decides for the agent |
|---|---|
| `core/best_next_action.py:36` | a utility function written as numbers — daemon down 100, tick error 90, failing tests 80, engineering task 59, doctrine document 58, external study 57, self-improvement failure 55, inbox debt 50. The largest weight wins |
| `core/autonomous_runtime.py:950` | the autonomous queue always opens with two prescribed tasks — inspect state, then plan and dry-run ingest — before any judgement is made about what is worth doing |
| `core/learning_planner.py:181` | attention has a fixed price list (100 / 95 / 70 / 55 / 40) and `core/learning_planner.py:366` holds a hardcoded set of core files to study when no focus is given |
| `core/subagent_registry.py:50` | five roles exist by default. The registry recommends keep / watch / pause / retire and never hires or fires |
| `core/team_plan.py:233` | a second, separate predefined workforce built from keywords, in which one watcher is added whenever a team is deemed necessary at all |
| `core/autonomous_runtime.py:74` | `spawn_subagent` is blocked on the unattended goal path, alongside `python_probe`, `rss_fetch`, `semantic_scholar_search`, `memory_bank`, `journal_append`. Web READ (`web_fetch`, `web_search`) was opened on that path by the operator's word on 2026-09-01 (`data/capability_events.jsonl`, second row); this row said «alongside the network tools» until 2026-09-03 (audit D2). Roles a human wrote in advance may run; a worker the agent decides it needs may not be created |
| `core/subagent_runner.py:380` | a subagent is a one-shot loop with `memory=None` and `persistent_store=None`. A temporary executor can be created; an employee who accumulates experience cannot |
| `core/charter_goal.py:14` | the agent picks its campaign goal itself, but the goal must anchor to a verbatim line of a document a human wrote. A one-time human push was replaced by a permanent human push stored in a file |
| `agent_tick.py:122` | on the unattended path `with_memory=False`, `episodic_replay=False`, and durable writes are restricted to a two-entry allowlist |

The pattern is not five imposed subagents. It is a layer of
developer-written heuristics sitting between the mission and the agent's
decisions, in at least nine separate places.

## What is permitted during the freeze

Only work directly needed to:

- find every place where code decides instead of the agent;
- classify each such mechanism as Constitution, Capability, or Agent Decision;
- remove or convert hidden executive decisions;
- restore one closed autonomous decision loop;
- wire memory and experience into subsequent decisions;
- prove that tasks, goals, roles and organisational structure are actually
  derived by the agent from observed state, mission and accumulated experience;
- preserve human sovereignty, safety boundaries, budget limits and the ban on
  self-widening authority;
- test and prove those properties.

Fixing an existing mechanism is allowed: it may be corrected, deleted,
simplified or rewired. Compensating for the problem by adding another planner,
manager, controller, memory layer, agent role or orchestration module is not.
A new abstraction is admissible only when a specific proven defect cannot be
fixed without it, and that must be argued separately.

## What lifts the freeze

Not "the code looks more autonomous", and not a green suite. Only an
end-to-end demonstration:

    observe state -> retrieve relevant experience -> identify gap or
    opportunity -> form candidate goals -> choose action -> choose or create
    the required capability or agent -> execute within authority -> verify
    outcome -> record experience -> the experience changes a later decision

And within that loop, the provenance of every significant decision must be
answerable: who decided this, and on what grounds? If the answer is a hidden
default, a hardcoded role, a fixed agenda, a fixed priority table, a charter
instruction or any other pre-written executive choice, that stretch is not yet
fixed.

One further condition, easy to lose: on a fresh state the agent is under no
obligation to perform work that was arranged in advance. It must be able to
conclude **no justified action** — but only in the narrow sense, and the
distinction matters enough to spell out:

    means      no justified EXPENSIVE or EFFECTFUL action right now, so
               observe, wait, and stay cheap
    NEVER means  no assignment arrived, therefore my activity is over

The first is a subject choosing not to spend. The second is a stop state, and
building it would reintroduce exactly the dependence on a human hand that this
freeze exists to remove. Idle is a posture of the running subject, not the end
of its life; what must never require a human is the decision to leave it.

Until these conditions are met the autonomous mode is not to be started as a
working mode, and nothing beyond this task is to be built.

## The second invariant in detail

Added 2026-08-20, from the same root. The first invariant is about who decides.
This one is about who lives.

Two clarifications the invariant carries. Diagnostic and test harnesses may
exist, but they are not alternative production identities or autonomous
execution roots: a shell, a future desktop window, the HTTP API and any
messaging adapter are doors to the same agent. And a normal owner must not have
to choose between auto-run, campaign, work-session, tick and daemon for the
autonomous agent to live.

Note what this invariant is NOT. It is not "one Python process never dies" —
the subject has to survive a reboot, a crash, an update, its own repair and a
power cut, so an immortal process would be the wrong requirement. Nor does it
mean thinking continuously: **alive is not the same as calling a model.** A
subject may sit in a cheap idle for hours, waiting on an event, a timer or a
result, and only reach for a model when there is a reason to. Being autonomous
means it does not need a human to decide when to leave idle.

### What is there today, read from the code

Four places construct an agent of their own: `agent_tick.py`, `api/server.py`,
`cli/one_shot.py` and `cli/app.py`, all through the shared builder in
`app/bootstrap.py`. Three more executables delegate rather than construct —
`main.py`, `app/windows_service.py`, `docker/daemon_loop.py`.

Sharper than the count of sites: **one tick constructs up to three agents** —
the task-drain agent (`agent_tick.py:946`), the self-build producer
(`agent_tick.py:680`) and the hygiene agent (`agent_tick.py:1165`) — each with
its own trace id and its own independent load of every store, and the campaign
lane builds a fourth (`agent_tick.py:1352`). So the shape today is not one
agent that drains a queue, keeps house and develops itself; it is several
temporary cognitive instances working over one heap of shared durable state.
The full map is in `docs/audit/archive/LIFECYCLE_OWNERSHIP_MAP.md`.

The installed production path is the one that matters most:
`scripts/install_daemon.ps1` registers a Windows Scheduled Task that runs
`agent_tick.py` **every 30 minutes**. **A correction, because the obvious reading of that is too strong.** A
short-lived process CAN load durable state from disk, continue an identity and
save it again before exiting; `with_memory=False` is not an inevitable
consequence of a tick architecture, and saying so would build a new dogma on
top of the old one. What is true today is narrower and still enough: each tick
constructs a NEW in-memory `AgentLoop`, and no continuous owner of working
state exists between ticks, so continuity must either be reconstructed from the
durable stores or is lost. On that path it is largely lost —
`agent_tick.py:122`.

On top of that, `app/runtime_cli.py` lets the human choose between `auto-run`,
`work-session` and `campaign-start`, with flags for tests, reflection, goal
inclusion, cycle counts and limits. As an engineering harness that is useful.
As the way the organism exists, it is the same trap in another form: the human
chooses in which manner the agent shall be autonomous today.

### What follows, and what does not

This is recorded, not acted on. Nothing is to be built now — no launcher, no
service, no tray icon, no messaging adapter. When the freeze lifts, the
question to answer is which of the existing runtime paths becomes the single
one, and whether each of the others is subordinated to it, demoted to a
diagnostic harness, or deleted.

The operational criterion to hold against any future design: **the machine is
on, therefore the agent is alive** — observing, thinking, acting when there is
justified action, remembering, learning, and telling its owner only what is
significant. Not "the owner started the right combination of modules and
flags", and not "Windows grants it a new small life every thirty minutes".

### Restart is continuity, not rebirth

Added to the lift condition, 2026-08-20. Restoring RAM bit for bit is
meaningless; what has to survive is the semantic life of the subject. After a
restart or a reboot it must be provable that:

    identity before            == identity after
    active commitments before  == recoverable after
    relevant memory before     == accessible after
    unfinished reasoning/work  == represented after
    authority before           == authority after

And memory must belong to the SUBJECT, not to a launch mode. The question that
matters after a reboot is not what the API remembers, or the campaign, or the
last shell session — it is what **the agent** remembers: which commitments are
still open, what it was doing and why, which hypothesis it was testing, what
the owner told it and what it understood from that, which workers exist and
why it created them.

### Why the two are one freeze

Fixing only the first — who decides — leaves a subject that reasons
autonomously and whose continuity nothing owns, so each new build has to
reconstruct it or lose it. Fixing only the second leaves a
very long-lived executor of somebody else's script, with `_build_queue`, preset
roles and a priority table intact. Neither property is worth much without the
other, which is why one freeze covers both.

## What the experiment behind this freeze is actually for

Stated by the operator, 2026-08-20. The freeze is not housekeeping before a
demo. It exists so that one specific experiment becomes readable: **remove the
external time frame** — no thirty minutes, no four hours, no N cycles — and
observe the trajectory of a system left to its own consecutive decisions.

Three properties have to hold at once, and they are separable:

    SELF-ACTION        it continues of its own accord
    SELF-PRESERVATION  its own actions do not destroy its ability to continue
    SELF-IMPROVEMENT   accumulated experience statistically improves later
                       decisions

The first alone is a machine that runs. The first two are a stable autonomous
machine. Only all three are development.

The interesting outcomes are not the two poles. Between "it destroys itself"
and "it becomes more capable" sit the likely ones: it circles; it produces
documents instead of progress; it keeps improving parts that were already good
enough; it fills memory with its own refuse; it repeats the same
investigations; it spends the budget without gaining capability; it grows more
complex without growing more useful; it simply stabilises and stops finding
directions. Each of those is a real result and each must be distinguishable
from the others.

So the question is not "will it survive". It is: **what happens to the quality
of the system across an unbounded sequence of its own decisions?** At action
10, at 100, at 1000 — is its memory an asset or a landfill; is its code clearer
or more tangled; has it stopped repeating old mistakes; has it learned to
abandon its own bad ideas; has it found ways to be useful that nobody wrote
down for it?

**A measured prerequisite, before any of that can be read.** SELF-IMPROVEMENT
is only observable if experience from an early cycle can change a decision at a
later one. It can — and MIR-115 measures what it would carry: of 136 episodes
currently eligible to steer later answers, 127 hold zero verified chunks and
are admitted by an unconditional exemption that the self-build machinery grants
its own output. Nine of 136 would pass the documented rule. Run the experiment
against that instrument and a landfill would be indistinguishable from
learning.

## Operator-ordered exceptions since the freeze (recorded 2026-08-28)

The freeze was declared by the operator, and the operator's later words
outrank it — but a freeze whose exceptions go unrecorded reads as an absolute
it no longer is. The second examiner (Codex) caught exactly that on
2026-08-28: new organs existed while this document still said "nothing new is
to be built". This ledger repairs the bookkeeping, not the breach; each entry
names the word that opened it and how it stands to the two invariants.
Repairs of registered defects were never frozen and are not listed.

| built after 2026-08-20 | operator's word | vs. the invariants |
|---|---|---|
| causal-climb ladder, slices 1–3 (`core/causal_climb_action.py`, MIR-096) | «бери орган MIR-096», «бери слайс 1/2/3» (2026-08-28) | serves I: the agent explains/refutes its own failures instead of receiving verdicts |
| mentor question channel (`core/mentor_channel.py`, MIR-178) | «бери третий канал» (2026-08-27) | serves I with a recorded non-interference boundary; advisory only |
| DeepSeek provider wiring | «положил $5 на deepseek — запусти и посмотрим» (2026-08-28) | generic capability — the class the freeze explicitly permits |
| conversation judge, stage 1 (`core/conversation_contract.py`) | «надо доделать вторую половину человеческого разговора» (2026-08-28) | neither invariant; a human-boundary form contract, developer-owned by its own honest table — the freeze's core stays unresolved by it |
| tools/memory_bank.py | «самое первое - долговременная память: он должен запоминать то, что положено... сам» (2026-08-31) | serves invariant I — the agent itself persists and recalls what it is supposed to remember, without external verdicts; BLOCKED for the unsupervised path |
| tools/journal_append.py | «Сначала реестр... пусть агент сам сначала вычитает реальные capabilities инструмента» (2026-09-01) | Appends state-journal entries under lock and envelope, born from the belt census that no tool can append; the agent must first read the tool's real capabilities from the registry before relying on this append. ; BLOCKED for the unsupervised path |
| core/self_stop_record.py | «Разрешить автономную запись self-stop events — ДА, но только через новый узкий capability, технически неспособный стать общей памятью или произвольной записью» (2026-09-01) | Этот орган записывает факт собственной остановки (отказ выбора цели, бюджетная стена) в один узкий журнал; поля приходят из рантайма, подпись стены считается кодом, без источника запись запрещена; LIVE on the unsupervised path as exactly the narrow capability the quote demands — one journal, fields from the runtime, no free text (this cell read «BLOCKED» beside the operator's «ДА» until 2026-09-03, audit D3) |
| core/step_references.py | «достроить то, что недостроено, и посмотреть, как он отреагирует» (2026-09-01) | Carries a MEASURED value from one plan step into the next (`{{step:<id>.output}}`), resolved only after the source ran and never filled with something plausible; removes the structural need to invent what could not be read — serves invariant I by letting evidence, not guesswork, reach the next action. Built by Fable at the operator's instruction. |
| core/capability_events.py | «Исправь только семантику repeat guard: факт упоминания темы не должен означать, что работа по ней завершена» (2026-09-02) | The machine carrier of "the world changed": one row per change of the unattended blocked-tools set. The repeat guard compares a theme's last WORK with the last change, so a "nothing left to do" verdict issued in another world no longer locks the theme — recentness stops meaning completion. Built by Fable under the operator's narrow grant. |
| core/model_routing_policy.py, tools/model_route.py | «Ключи остаются недоступными агенту как секреты. Агент не назначает роли ключам. Он должен самостоятельно, в пределах разрешённого пула провайдеров, формировать и обновлять роли моделей по наблюдаемому качеству, стоимости и типу задачи… Жёсткие привязки ролей в .env не должны быть источником истины» (2026-09-04 22:20) | Serves invariant I directly: which model answers which role was a developer decision pinned in .env (and, below it, a selection policy ranked in code); now it is HIS decision, journaled with a reason and evidence under `agent_policy:<id>`, read by the router ahead of the env pins, within a pool the operator bounds (providers with a client and a present key). The code stores and validates the form of a decision and substitutes the credential; it never ranks the models for him. Prior art read first: RouteLLM, FrugalGPT, LiteLLM's router (verdicts in the module docstring). |
| tools/memory_recall.py | «чтение памяти как low-trust явное извлечение… А так тебя устраивает? Да» (2026-09-04, via Кодекс) | The read half of the memory door opened the same morning (memory_bank): a bounded search of his own durable records — at most 5, active store only, newest first, each marked LOW-TRUST with its source; an unreadable store is «unavailable». Serves invariant I: without an explicit read the exam STORE→RETRIEVE→USE cannot be sat, because passive injection is the loop's choice, not his. No new authority beyond reading what he himself wrote. |
| core/model_roster.py, tools/model_roster.py | «Ставь глаз первым после полосы сегодня в окно… он увидеть должен четыре ключа, а видеть только один ключ» (2026-09-04) | A measurer, not an organ: the agent had no perception of his own providers (no context block named one; a key was learned only by its death). Facts only — key present/absent without the value, roles, health, cost tier, spend today, day ceiling; unknown where a source is missing. No switch built: which model he chooses is his discernment, measured by prediction P5, not configured. |
| core/burn_in_sandbox.py | «Add an explicit SANDBOX-ONLY autonomous self-repair mode… must require an explicit sandbox/burn-in configuration; default production path remains fail-closed… Do not create a hidden bypass around PolicyGate. This must be a first-class, explicit experimental authority mode» (2026-09-17) | НОВОЕ ПОЛНОМОЧИЕ, и оно названо прямо: в изолированной копии агент вправе применить правку КОДА без человека. Включается двумя независимыми жестами разной природы — переменная окружения `AGENT_BURN_IN_SANDBOX=on` (жест запускающего процесс) и файл `config/burn_in_sandbox.json`, называющий АБСОЛЮТНЫЙ путь именно этой копии, срок и суточный потолок (жест готовившего копию). Поодиночке ни один не полномочие; требование «метка называет свой путь» закрывает единственный опасный случай — песочницу склонировали в производство вместе с меткой. Границы, которые полномочие НЕ раздвигает: сухость прогона старше него; применение идёт ТОЙ ЖЕ полосой (`run_self_apply_lane`) с прицельными тестами, полной батареей, отдельной веткой, локальным коммитом и откатом при красной проверке; push и merge в полосе нет; писать можно только внутри копии; забор (метка, этот модуль, `policy_gate.py`, `actuation_gateway.py`, `self_apply_lane.py`, установщик демона) не двигается изнутри; запрещённые классы полосы остаются запрещёнными; расход считается ТЕМ ЖЕ журналом, что у стоячего гранта. Производственный путь остаётся байт-в-байт прежним: без полномочия `load_sandbox_authority` возвращает None при любом сомнении. Против инвариантов: служит II — эксперимент измеряет самопочинку, а не скорость упирания в ящик человека. |
| core/burn_in_sandbox.py (профиль памяти) | «Allow verified autonomous learning to persist in the sandbox profile… Production unattended-memory policy must remain unchanged. No direct ungoverned writes» (2026-09-17) | Безнадзорный профиль памяти расширяется РОВНО на два стока — `procedure` и `knowledge` — и только при включённом полномочии выше. Всё прочее закрыто прежним умолчанием-запретом; сухость прогона и режим аудита стоят ВЫШЕ списка по порядку правил `_durable_learning_suppressed`; каждая запись по-прежнему проходит `MemoryWritePolicy`. Производственный `UNATTENDED_MEMORY_PROFILE` не изменён ни в одной строке. |
| core/burn_in_supervisor.py | «после `committed_local` sandbox должен уметь безопасно сделать именно проверенный commit следующим активным состоянием эксперимента, не трогая `main`, не push/merge и не позволяя произвольному коду выбрать себе любой commit» (2026-09-18, ревизия PR #333, дефект 5) | ПРИНИМАЮЩИЙ, и он нарочно ВНЕ полосы. Полоса (`core/self_apply_lane.py`) по-прежнему возвращает дерево на исходную ветку — это не дефект, а разделение полномочий: код, который сам себя меняет, не вправе сам себя принимать. Полоса лишь ПРЕДЪЯВЛЯЕТ проверенный SHA в реестре; предъявление прав не даёт. Принимающий перепроверяет заново и требует ВСЕГО ПЯТИ: (1) имя — полный сорокзначный SHA, а не ссылка, потому что ссылка разрешается в разное в разное время; (2) SHA предъявлен полосой — иначе «принять» означало бы «взять любой commit из истории»; (3) это ШАГ: единственный родитель кандидата есть нынешняя голова опыта, так что два кандидата от одной головы дают одно принятие и один отказ, а не увод опыта вбок; (4) кандидат не трогает забор — ворота, счетовода полномочия, полосу и САМОГО принимающего; (5) батарея зелена в СВЕЖЕМ рабочем дереве на этом SHA, а не по словам полосы и не в старом дереве. Голова опыта — файл `state/burn_in_head.json`, а НЕ ветка: `main` и текущая ветка человека не двигаются. Список разрешённых глаголов git (`_VERBS`) не содержит ни `push`, ни `fetch`, ни `remote` — как и `core/safe_vcs.py`, это список разрешённого, а не забытого. Про неизменяемость честно: модуль лежит в том же дереве и абсолютно неизменяемым быть не может; закрыто это двумя разными запретами — правило 4 отказывает кандидату, трогающему этот файл, а процесс принимающего человек запускает из исходного checkout и тот себя по ходу опыта не перечитывает. Против инвариантов: служит II — впервые получается последовательность ошибка → ремонт → тесты → принятие → новый работающий агент → следующая ошибка. |

Modules born of surgery on registered defects (repairs were never frozen):
`anatomy_groups.py` (MIR-180 data relocation), `autonomous_runtime_proposals.py`
and `autonomous_runtime_types.py` (the oversized-runtime split),
`command_subjects.py` (MIR-158), `repo_provenance.py`, `rule_approved_apply.py`
(MIR-173/175), `spend_report.py` (the spending mirror), `subagent_quarantine.py`,
`anatomy_sync.py` (2026-09-04: the anatomy-map sync moved out of the producer
when its repair — render the proposal's map through the generator, so a split
with a new module stops rolling back on the anatomy guard — pushed the file
past its size ratchet). Incremental-split helpers born of the agent's own
oversized-module defects, each split approved by the operator's word on
2026-09-04 and applied through the lane by his hands: `smart_memory_helpers.py`,
`operator_intent_patterns_helpers.py`, `best_next_action_helpers.py`.
`bounded_subprocess.py` (2026-09-05: the tree-killing bounded runner grown in
`tools/shell_exec.py` for the `git blame` stall, moved to `core/` when the
exam's turn 42 found the same 600-second hang in `repo_provenance.py`'s
`git ls-files`; a repair, shared — no new authority).
`success_check.py` (2026-09-17, ремедиация autonomy burn-in audit, находка 5:
«ответ — не выполнение». Единственная обязанность органа — прочитать названный
целью след с диска и вернуть вердикт `verified` / `missing` / `unverifiable`.
Новых полномочий нет: он только читает, ничего не пишет и ничего не решает о
повестке. Закрывает дефект, при котором единственным признаком «сделано» был
непустой текст модели, то есть «я создал файл» и созданный файл были одним
событием. Исход `unverifiable` сохраняет прежнее поведение и НАЗЫВАЕТ его —
«не проверяли» вместо «сошлось»).

This ledger carries a DISCLOSURE SENSOR, not a permission gate — the honest
name is the second examiner's: since 2026-08-28,
`tests/test_the_freeze_gates_new_organs.py` reddens the battery for any .py
file under core/, cli/, app/, tools/ or api/ (subdirectories included) that
did not exist at the freeze baseline (`1577b85`) and is not named in this
document. What that forces is the RECORD, not the prior permission: prior
permission is a process property (the operator's word before the build), and
a test living in the same tree as the code cannot outrun it. New behaviour
inside an old module stays invisible to a structural sensor; both live
runners have full history (the home clone; CI fetches depth 0), so the
history-unavailable skip applies only to third-party shallow clones.

The blocking issue of this document — WHO DECIDES and WHO LIVES — remains
open and is not diminished by any row above.
