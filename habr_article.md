# 46 слоёв автономного AI-агента: архитектура, контракты и реальный код

> Как мы проектировали автономного агента с 46 системными слоями, зачем нам межкомпонентные контракты и какие 4 проблемы мы поймали до того, как они сожрали бюджет.

## Зачем вообще автономный агент

Когда ChatGPT научился отвечать на вопросы, многие подумали: «А что, если дать ему память, инструменты и петлю обратной связи?» Мы тоже подумали — и построили агента с нуля.

Не обёртку над API. Не LangChain-пайплайн из трёх кубиков. Полноценную систему с 46 слоями: от восприятия информации до причинно-следственного мышления, от управления целями до самовосстановления после сбоев.

В этой статье я расскажу:

- как устроена архитектура — все 46 слоёв по группам;
- как работает планировочный пайплайн (4 ключевых компонента);
- какие 4 проблемы мы нашли при интеграции — и как их решили кодом;
- как 55 контрактных тестов не дают системе деградировать.

---

## Архитектура: 46 слоёв

Полный автономный агент — это не один файл и не один промпт. Это 46 системных блоков, разбитых на три группы.

### Ядро (слои 1–20)

Эти слои — скелет, без которого агент не работает:

| Слой | Название | Что делает |
| ---- | -------- | ---------- |
| 1 | **Perception** | Веб-краулер, парсер документов, API-клиент, распознавание речи |
| 2 | **Knowledge System** | Векторная БД, эмбеддинги, кратко/долгосрочная/семантическая память |
| 3 | **Cognitive Core** | Рассуждения, планирование, генерация кода, анализ |
| 4 | **Agent System** | Ролевые агенты: кодер, аналитик, планировщик, отладчик |
| 5 | **Tool Layer** | Браузер, терминал, FS, Python, GitHub API, Docker, облака |
| 6 | **OS Layer** | Управление файлами, процессами, сетью, пакетами |
| 7 | **Software Dev** | Генерация, анализ, тесты, билд, зависимости |
| 8 | **Execution** | Запуск скриптов, команд, деплой |
| 9 | **Learning** | Чтение статей, документации, кода; обновление знаний |
| 10 | **Reflection** | Постмортемы, анализ ошибок, оценка решений |
| 11 | **Self-Repair** | Обнаружение сбоев, генерация патчей, перезапуск процессов |
| 12 | **Self-Improvement** | Оптимизация алгоритмов, стратегий, архитектуры |
| 13 | **Package Manager** | Поиск, установка, обновление зависимостей |
| 14 | **Multilingual** | Перевод, понимание терминологии на разных языках |
| 15 | **Communication** | Telegram, веб-интерфейс, API, мобильный клиент |
| 16 | **Security** | Секреты, контроль доступа, аудит |
| 17 | **Monitoring** | Логи, метрики, трассировка |
| 18 | **Orchestration** | Очереди, приоритеты, параллельное выполнение |
| 19 | **Reliability** | Retry, timeout, fallback, recovery |
| 20 | **Autonomous Loop** | observe → analyze → plan → act → evaluate → learn → improve → repeat |

### Управленческие слои (21–26)

Без них агент формально работает, но хаотично:

| Слой | Название | Зачем |
| ---- | -------- | ----- |
| 21 | **Governance / Policy** | Правила и ограничения поведения |
| 22 | **Human Approval (HITL)** | Подтверждение человеком опасных действий |
| 23 | **State & Session** | Checkpoint, resume, идемпотентность |
| 24 | **Data Validation** | Контракты, валидация входов/выходов |
| 25 | **Evaluation** | Бенчмарки, KPI, регрессия |
| 26 | **Budget Control** | Лимиты токенов, бюджета, latency |

### Расширенные слои (27–46)

Слои, превращающие агента из «инструмента» в «партнёра»:

| Слой | Название | Зачем |
| ---- | -------- | ----- |
| 27 | **Environment Modeling** | Модель окружения, прогноз последствий |
| 28 | **Sandbox Testing** | Тестирование действий до реального выполнения |
| 29 | **Skill Library** | Хранение и переиспользование стратегий |
| 30 | **Task Decomposition** | Разбивка задач → граф подзадач |
| 31 | **Knowledge Acquisition** | Автоматический поиск и пополнение знаний |
| 32 | **Model Management** | Выбор LLM под задачу, fallback, версионирование |
| 33 | **Data Lifecycle** | Дедупликация, архивация, чистка данных |
| 34 | **Distributed Execution** | Масштабирование по узлам и машинам |
| 35 | **Capability Discovery** | Поиск новых инструментов и библиотек |
| 36 | **Experience Replay** | Повторный анализ прошлого опыта |
| 37 | **Goal Management** | Дерево целей, приоритизация, конфликты |
| 38 | **Long-Horizon Planning** | Дорожные карты на недели/месяцы, MDP-планирование |
| 39 | **Attention & Focus** | Фильтрация шума, концентрация на важном |
| 40 | **Temporal Reasoning** | Дедлайны, расписания, последовательности событий |
| 41 | **Causal Reasoning** | Причинно-следственный анализ |
| 42 | **Ethics & Values** | Этические границы, предотвращение вреда |
| 43 | **Social Interaction** | Понимание намерений, управление диалогом |
| 44 | **Hardware Interaction** | Датчики, периферия, устройства |
| 45 | **Identity & Self-Model** | Знание собственных возможностей и ограничений |
| 46 | **Knowledge Verification** | Проверка источников, поиск противоречий |

---

## Планировочный пайплайн: 4 компонента

Самая интересная часть архитектуры — как агент ставит цели, планирует, раскладывает на задачи и выполняет.

Вот цепочка вызовов:

```text
AutonomousLoop (L20)
  ├─ PLAN:  GoalManager.get_next() / activate()
  │         GoalManager.decompose() → TaskDecomposition.decompose()
  │         LongHorizonPlanning.plan() (каждые 20 циклов)
  └─ ACT:   Orchestration.run_next()
```

### 1. GoalManager (слой 37) — владелец дерева целей

GoalManager — единственный компонент, который может создавать, менять статус и удалять цели. Все остальные компоненты **только читают** или **предлагают**.

```python
class GoalManager:
    """
    OWNERSHIP CONTRACT:
        Владеет: _goals (дерево целей), _active_goal_id
        Только GoalManager может создавать / менять статус / удалять Goal.
        LongHorizonPlanning НЕ пишет в _goals — только предлагает через
        promote_advisory().
        TaskDecomposition и Orchestration читают goal описание,
        но не мутируют Goal-объекты.
    """
    PROMOTION_EU_THRESHOLD = 0.55

    def get_next(self) -> Goal | None:
        """Выбирает следующую цель по urgency_score."""
        candidates = [
            g for g in self._goals.values()
            if g.status in (GoalStatus.PENDING, GoalStatus.ACTIVE)
            and not g.sub_goals  # листовые цели
        ]
        chosen = max(candidates, key=lambda g: g.urgency_score)
        self._trace('get_next', goal_id=chosen.goal_id,
                    urgency=round(chosen.urgency_score, 3),
                    reason=f'max urgency из {len(candidates)} кандидатов')
        return chosen
```

`urgency_score` — производная от приоритета и оставшегося времени до дедлайна:

```python
@property
def urgency_score(self) -> float:
    base = (6 - self.priority.value) / 5.0  # 1.0 для CRITICAL, 0.2 для DEFERRED
    if self.deadline:
        remaining = max(0, self.deadline - time.time())
        urgency = 1.0 / (1.0 + remaining / 3600)
        return (base + urgency) / 2
    return base
```

### 2. LongHorizonPlanning (слой 38) — дорожные карты

Создаёт Roadmap-ы — планы на недели и месяцы. Для стратегических задач использует MDP-фрейминг: состояния, действия, переходы, функция награды.

У каждого Roadmap есть TTL:

```python
class Roadmap:
    _HORIZON_TTL_MULTIPLIER = 2

    def __init__(self, roadmap_id, title, goal, horizon, ...):
        horizon_days = {
            HorizonScale.DAY: 1, HorizonScale.WEEK: 7,
            HorizonScale.MONTH: 30, HorizonScale.QUARTER: 90,
            HorizonScale.YEAR: 365,
        }.get(horizon, 30)
        self.ttl_sec = max(86400, horizon_days * 86400 * self._HORIZON_TTL_MULTIPLIER)

    def is_stale(self) -> bool:
        return time.time() - self.created_at > self.ttl_sec
```

Метрики планирования — `PlanMetrics` — считают expected utility как взвешенную сумму:

```python
def expected_utility(self) -> float:
    reward = (
        self.resource_efficiency * 0.25
        + self.survival_probability * 0.30
        + self.influence_score * 0.25
        - self.risk_score * 0.20
    )
    return round(max(0.0, min(1.0, reward)), 3)
```

### 3. TaskDecompositionEngine (слой 30) — граф подзадач

Раскладывает цели на подзадачи с зависимостями. Ключевое — ограничения на рекурсивное расширение:

```python
class TaskGraph:
    def expand_node(self, task_id, subtasks,
                    max_depth=4, max_nodes=64) -> bool:
        original = self._nodes.get(task_id)
        new_depth = (original.depth + 1) if original else 0

        if new_depth >= max_depth:
            self._rejection_reason = ('MAX_DEPTH', new_depth, max_depth)
            return False
        if len(self._nodes) + len(subtasks) > max_nodes:
            self._rejection_reason = ('MAX_TOTAL_NODES',
                                      len(self._nodes) + len(subtasks), max_nodes)
            return False

        self._rejection_reason = None
        if original:
            original.status = SubtaskStatus.SKIPPED
        for sub in subtasks:
            sub.depth = new_depth
        ...
        return True
```

`MAX_DEPTH=4` и `MAX_TOTAL_NODES=64` — это не магические числа. Без них одна сложная задача разбивалась на подзадачи, те на подподзадачи, и дальше рекурсивно — до нескольких тысяч узлов.

### 4. OrchestrationSystem (слой 18) — выполнение с бюджетом

Принимает задачи через `submit()`, складывает в очередь с приоритетами, выполняет через `run_next()`. Три ограничения:

```python
class OrchestrationSystem:
    """
    BUDGET CAPS:
        MAX_QUEUE_SIZE   = 100  — максимум задач в очереди
        TASK_MAX_AGE_SEC = 3600 — TTL pending-задачи (1 час)
        BUDGET_CAP_SEC   = 300  — макс суммарного времени за цикл (5 мин)
    """

    def _execute(self, task):
        t_start = time.time()
        try:
            if self.agent_system:
                result = self.agent_system.handle({
                    'goal': task.goal,
                    'role': task.role,
                    **task.metadata,
                })
                ...
        finally:
            elapsed = time.time() - t_start
            self._budget_spent += elapsed
            self._trace('execute_end', task_id=task.task_id,
                        status=task.status, elapsed_sec=round(elapsed, 2),
                        budget_spent=round(self._budget_spent, 2))
```

Бюджет сбрасывается в начале каждого цикла `AutonomousLoop`:

```python
def reset_budget(self):
    """Сброс бюджета в начале нового цикла."""
    self._budget_spent = 0.0

@property
def budget_exhausted(self) -> bool:
    return self._budget_spent >= self.BUDGET_CAP_SEC
```

---

## 4 проблемы, которые мы нашли (и как решили)

При интеграции четырёх компонентов мы обнаружили 4 «дрифт-риска» — ситуации, когда система формально работает, но незаметно деградирует.

### Проблема 1: Семантический дрифт целей

**Симптом.** GoalManager передаёт в Orchestration строку `goal.description`, но Orchestration передаёт дальше уже урезанную версию. Через 3 слоя пересказов цель теряла смысл.

**Решение.** Полный `parent_goal` хранится в `metadata` задачи и прокидывается без обрезки:

```python
task = orchestration.submit(
    goal=subtask.goal,
    metadata={'source_goal_id': parent_goal.goal_id}
)
```

`_trace` каждого компонента фиксирует `source_goal`, и можно восстановить цепочку: какая цель породила какую задачу.

### Проблема 2: Устаревшие advisory (stale roadmaps)

**Симптом.** LongHorizonPlanning создавал дорожную карту. Через неделю цель менялась, но старая карта оставалась активной. Агент продолжал следовать плану, который уже не соответствовал реальности.

**Решение.** Двойной механизм инвалидации:

```python
class LongHorizonPlanning:
    def invalidate_stale_roadmaps(self) -> int:
        """Архивирует карты, у которых истёк TTL."""
        stale_ids = [
            rm_id for rm_id, rm in self._roadmaps.items()
            if rm.status == 'active' and rm.is_stale()
        ]
        for rm_id in stale_ids:
            rm = self._roadmaps[rm_id]
            rm.status = 'stale'
            self._trace('invalidate_stale', roadmap_id=rm_id, ...)
        return len(stale_ids)

    def invalidate_for_goal(self, old_goal: str) -> int:
        """Архивирует карты, привязанные к сменившейся цели."""
        invalidated = 0
        for rm in self._roadmaps.values():
            if rm.status == 'active' and rm.goal == old_goal:
                rm.status = 'invalidated'
                invalidated += 1
                self._trace('invalidate_goal_switch', ...)
        return invalidated
```

`invalidate_stale_roadmaps()` вызывается автоматически перед созданием новой карты. `invalidate_for_goal()` — при смене активной цели.

### Проблема 3: Взрыв декомпозиции

**Симптом.** TaskDecomposition разбивала задачу на 5 подзадач. Каждая подзадача оказывалась «слишком сложной» и расширялась ещё на 5. При глубине 8 это 5^8 = 390 000 узлов. Система зависала.

**Решение.** Два жёстких лимита в `expand_node()`:

```python
MAX_DEPTH = 4        # максимум уровней вложенности
MAX_TOTAL_NODES = 64 # максимум узлов в одном графе
```

И `_rejection_reason` для диагностики:

```python
if new_depth >= max_depth:
    self._rejection_reason = ('MAX_DEPTH', new_depth, max_depth)
    return False
```

`AutonomousLoop` логирует отклонение:

```python
if not graph.expand_node(task_id, subtasks):
    reason = getattr(graph, '_rejection_reason', None)
    log.warning(f"expand_node отклонён: {reason}")
```

### Проблема 4: Сайд-задачи без цели

**Симптом.** Orchestration принимала задачи от разных источников. Некоторые задачи жили в очереди часами — их никто не забирал, но они занимали место и создавали шум в логах.

**Решение.** Три механизма:

1. **TTL**: задачи в статусе `pending` дольше 1 часа удаляются:

```python
def _cleanup_stale(self):
    now = time.time()
    for task in self._queue:
        if task.status == 'pending' and (now - task.created_at) > self.TASK_MAX_AGE_SEC:
            task.status = 'expired'
            self._trace('task_expired', task_id=task.task_id, ...)
```

1. **Бюджет**: не более 300 секунд суммарного execution-времени за один цикл.

1. **Лимит очереди**: не более 100 задач. При переполнении — принудительная очистка stale, затем отклонение.

---

## Протокол промоушена: roadmap → goal

Одна из самых нетривиальных задач — как LongHorizonPlanning предлагает дорожную карту на повышение до полноценной цели, а GoalManager решает, принять или нет.

Мы назвали это **promotion protocol**:

```text
LongHorizonPlanning.offer_promotion(roadmap) 
  → GoalManager.promote_advisory(roadmap.to_dict())
    → Проверка: EU ≥ 0.55? Не stale? Нет дубликата?
    → Если ОК: Goal создаётся с тегом promoted_from_roadmap
    → Если нет: _trace('promote_reject', reason=...)
```

Код промоушена в GoalManager:

```python
def promote_advisory(self, roadmap_dict: dict) -> Goal | None:
    eu = roadmap_dict.get('expected_utility', 0.0)
    if isinstance(roadmap_dict.get('metrics'), dict):
        eu = roadmap_dict['metrics'].get('expected_utility', eu)
    status = roadmap_dict.get('status', '')

    if status in ('stale', 'invalidated'):
        self._trace('promote_reject', reason=f'status={status}')
        return None

    if eu < self.PROMOTION_EU_THRESHOLD:
        self._trace('promote_reject', reason=f'EU={eu:.3f} < {self.PROMOTION_EU_THRESHOLD}')
        return None

    duplicate = self._find_duplicate_goal(goal_desc, parent_id=None)
    if duplicate:
        self._trace('promote_reject', reason=f'дубликат [{duplicate.goal_id}]')
        return None

    new_goal = self.add(description=goal_desc, priority=GoalPriority.HIGH,
                        tags=['promoted_from_roadmap', rm_id])
    self._trace('promote_accept', goal_id=new_goal.goal_id, eu=eu)
    return new_goal
```

Порог `EU = 0.55` — не случайный. При пороге 0.5 слишком много карт проходило в цели, при 0.6 — слишком мало. 0.55 отсеивает ~40% карт с низкими метриками.

---

## Traceability: каждое решение оставляет след

Каждый из четырёх компонентов имеет метод `_trace()`. Формат одинаковый:

```python
def _trace(self, action: str, **ctx):
    entry = {
        'ts': time.time(),
        'layer': 37,  # или 38, 30, 18
        'component': 'GoalManager',
        'action': action,
        **ctx
    }
    self._log(f"[TRACE] {entry}")
```

Это не декоративное логирование. Каждый `_trace` фиксирует **почему** было принято решение:

- `get_next`: какая цель выбрана и почему (urgency score, количество кандидатов)
- `promote_reject`: почему roadmap не стал целью (EU, stale, дубликат)
- `submit_accept` / `submit_reject`: почему задача принята/отклонена
- `execute_end`: сколько времени заняло, какой статус, сколько бюджета потрачено
- `task_expired`: почему задача удалена из очереди
- `expand_node_reject`: почему расширение дерева заблокировано (MAX_DEPTH / MAX_TOTAL_NODES)

---

## 55 тестов: контракты как код

Мы написали `test_pipeline_contracts.py` — 55 тестов в 8 категориях:

| Категория | Тестов | Что проверяет |
| --------- | ------ | ------------- |
| Integration | 2 | Полный цикл: Goal → Decompose → Submit → Execute |
| Promotion | 9 | EU threshold, stale/invalidated reject, duplicate, accept |
| Budget | 7 | Queue limit, TTL, budget cap, reset, exhaustion |
| Traceability | 8 | Все _trace вызовы генерируют записи |
| Ownership | 5 | Компоненты не мутируют чужое состояние |
| Failure | 11 | Пустые входы, None, некорректные типы, corrupt metrics |
| Soak (100 циклов) | 6 | Нет утечек, очередь не растёт, все задачи завершаются |
| Edge cases | 7 | Пограничные значения, пустые графы, дедупликация |

Пример теста промоушена:

```python
def test_promote_reject_low_eu(self):
    """Roadmap с EU ниже порога не промоутируется."""
    gm = _make_gm()
    metrics = PlanMetrics()
    metrics.resource_efficiency = 0.1
    result = gm.promote_advisory({
        'roadmap_id': 'rm0001',
        'goal': 'test goal',
        'status': 'active',
        'metrics': metrics.to_dict(),
    })
    assert result is None
```

Пример soak-теста:

```python
def test_soak_100_cycles_budget_resets(self):
    """100 циклов: бюджет корректно сбрасывается, утечки отсутствуют."""
    orch = _make_orch()
    for _ in range(100):
        orch.reset_budget()
        assert orch.budget_remaining == orch.BUDGET_CAP_SEC
        t = orch.submit(goal='soak cycle task')
        orch.run_next()
    assert orch.summary()['total'] == 100
```

Все 55 тестов проходят за 0.12 секунды. Ни один не использует сеть или LLM.

---

## Автономный цикл: observe → act → learn

Всё вышеописанное связывается в `AutonomousLoop` (слой 20). Каждый цикл — 8 фаз:

```text
OBSERVE  → собрать информацию из окружения
ANALYZE  → понять что происходит
PLAN     → выбрать цель, декомпозировать
SIMULATE → проверить план в песочнице
ACT      → выполнить через Orchestration
EVALUATE → оценить результат
LEARN    → извлечь урок
IMPROVE  → обновить стратегии
```

Уверенность каждой фазы пропагируется вниз:

```python
class LoopCycle:
    def __init__(self, cycle_id):
        self.confidence = {
            'observe': 1.0, 'analyze': 1.0, 'plan': 1.0,
            'simulate': 1.0, 'act': 1.0, 'evaluate': 1.0,
        }

    @property
    def overall_confidence(self) -> float:
        """Итоговая уверенность — минимум по всем фазам."""
        return min(self.confidence.values())
```

Если одна фаза выдала низкую уверенность — весь цикл помечается как ненадёжный, и агент может запросить подтверждение у человека (HITL, слой 22).

---

## Дорожная карта безопасной эволюции

Один из принципов проекта: **не добавлять всё сразу**. Архитектура включает 5 этапов развития с явными запретами:

**Этап 0 — базовая безопасность:**

- ✅ Policy-гейты для инструментов, сети, файлов
- ✅ Сквозной аудит: инициатор → цель → результат → риск
- ❌ Никогда: скрытые bypass авторизации, автоисполнение кода без sandbox

**Этап 1 — память без деградации:**

- ✅ Верификация знаний с карантинизацией
- ✅ Учёт происхождения: источник, время, доверие
- ❌ Никогда: автозапись непроверенных данных без меток

**Этап 2 — оркестрация и исполнение:**

- ✅ Устойчивые очереди с retry/backoff
- ✅ Идемпотентность шагов, checkpoint/resume
- ❌ Никогда: бесконечные retry, параллелизм без блокировок

**Этап 4 — самоисправление:**

- ✅ Двухшаговый repair: предложить → валидировать тестами
- ✅ Проверка в sandbox до прода
- ❌ Никогда: автоприменение патчей без валидации

**Глобальные запреты:**

- Эксфильтрация секретов и токенов
- Непрозрачные решения без журналирования
- Критичные действия без подтверждения человеком

---

## Что мы поняли

1. **Контракты важнее тестов.** Тесты проверяют код, контракты проверяют *границы между компонентами*. Без OWNERSHIP CONTRACT два компонента молча писали в одну структуру, и баги были невоспроизводимы.

2. **Traceability — не роскошь.** Когда агент выполняет 100 циклов перед тем, как вы посмотрите в логи, единственный способ понять «почему он это сделал» — structured trace.

3. **Бюджет — это не только деньги.** `BUDGET_CAP_SEC`, `MAX_QUEUE_SIZE`, `MAX_DEPTH` — всё это про один принцип: система не должна тратить бесконечные ресурсы на одну задачу.

4. **46 слоёв — это не overengineering.** Каждый слой решает одну проблему. Без слоя 11 (Self-Repair) агент не переживёт сбой API. Без слоя 26 (Budget Control) он сожжёт весь бюджет за ночь. Без слоя 22 (HITL) он удалит прод-базу.

---

## Ссылки

- Репозиторий: [github.com/...] *(заполнить)*
- Архитектура агента: 46 слоёв, ~180 Python-файлов, ~70K строк кода
- Стек: Python 3.13, OpenAI API, Claude API, HuggingFace, Telegram Bot API, FastAPI, Docker

---

*Если интересно узнать подробнее про конкретный слой (Self-Repair, Knowledge Verification, Experience Replay) — пишите в комментариях, расскажу.*
